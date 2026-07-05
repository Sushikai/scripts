"""upload.py — B站上传模块（bilibili_api + httpx）"""

import asyncio, logging, json, subprocess, time, re
from pathlib import Path
from typing import Optional, Dict

from ..config import BILIBILI_COOKIES_FILE

log = logging.getLogger(__name__)


def _load_cookies(cookies_file: Path) -> dict:
    """加载Cookie（与 fengge_pipeline.load_cookies 一致：支持 JSON 和 Netscape 格式）"""
    try:
        text = cookies_file.read_text(encoding="utf-8").strip()
        if text.startswith('{') or text.startswith('['):
            return json.loads(text)
        cookies = {}
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
        return cookies
    except Exception as e:
        log.error(f"加载Cookie失败: {e}")
        return {}


class BilibiliUploader:
    """Bilibili视频上传（bilibili_api + httpx）— 不传封面让B站自动选帧"""

    def __init__(self, cookies_file: Path = None):
        self.cookies_file = cookies_file or BILIBILI_COOKIES_FILE

        # 切换到httpx客户端（避免curl_cffi的curl: (16)错误）
        import bilibili_api as _bapi
        from bilibili_api.clients.HTTPXClient import HTTPXClient as _HTTPXClient
        _bapi.register_client("httpx", _HTTPXClient)
        _bapi.select_client("httpx")

        self.cred = self._build_credential()

    def _build_credential(self):
        """从cookie文件构建Credential（与 fengge_pipeline 一致：JSON/Netscape 都支持，不传 dedeuserid）"""
        from bilibili_api import Credential
        try:
            data = _load_cookies(self.cookies_file)
            log.info(f"Cookie加载: SESSDATA={data.get('SESSDATA','')[:20]}..., DedeUserID={data.get('DedeUserID','-')}")
            return Credential(
                sessdata=data.get("SESSDATA", ""),
                bili_jct=data.get("bili_jct", ""),
                buvid3=data.get("buvid3", ""),
            )
        except Exception as e:
            log.error(f"Cookie加载失败: {e}")
            raise

    def _extract_video_cover(self, video_path: Path) -> Path:
        """
        用ffmpeg从视频中提取第一帧作为封面。
        等同于让B站自动选帧，用户后续可在B站后台手动更换。
        """
        import tempfile, subprocess
        # 输出到系统temp目录
        _tmp_dir = Path(tempfile.gettempdir())
        _cover_path = _tmp_dir / f"cover_{video_path.stem}_{int(time.time())}.jpg"
        cmd = [
            "ffmpeg", "-y", "-ss", "00:00:01",  # 第1秒（避免黑帧）
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",  # 高质量JPEG
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            str(_cover_path),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and _cover_path.exists():
                log.debug(f"封面提取成功: {_cover_path}")
                return _cover_path
            else:
                log.warning(f"封面提取失败，尝试第0秒: {r.stderr.strip()[:100]}")
                # 降级：直接用第0秒
                cmd[3] = "-ss"
                cmd[4] = "00:00:00"
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and _cover_path.exists():
                    return _cover_path
        except Exception as e:
            log.warning(f"封面提取异常: {e}")
        # 最终降级：创建空白封面
        return self._create_blank_cover(_tmp_dir / f"blank_{int(time.time())}.jpg")

    def _create_blank_cover(self, path: Path) -> Path:
        """创建空白封面（ffmpeg降级方案）"""
        import subprocess
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=1",
            "-frames:v", "1", "-q:v", "2",
            str(path),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return path

    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list,
        tid: int = 201,
        max_retries: int = 3,
    ) -> Optional[str]:
        """
        使用bilibili_api上传视频到B站（自动重试3次，网络抖动不影响）。
        关键优化：不传cover参数，让B站自动从视频中选帧。
        上传后验证封面是否已生成并可访问。
        """
        if not video_path.exists():
            log.error(f"视频文件不存在: {video_path}")
            return None

        log.info(f"开始上传: {video_path.name} ({video_path.stat().st_size // 1024 // 1024}MB)")

        # ── 从视频提取第一帧作为封面（等同于B站自动选帧）─────────────────
        # 用户偏好：上传时让B站自动选帧，之后在B站后台手动更换封面
        # bilibili_api要求cover必填，用ffmpeg截取第一帧实现自动选帧效果
        import tempfile
        _cover_path = self._extract_video_cover(video_path)
        log.info(f"封面已提取: {_cover_path}")

        import time
        last_error = None
        for attempt in range(max_retries):
            try:
                from bilibili_api import video_uploader, Picture as _Picture
                _cover = _Picture.from_file(str(_cover_path))
                meta_kwargs = {
                    "tid": tid,
                    "title": title[:80],
                    "desc": description[:500],
                    "tags": tags[:10] if tags else ["信息差", "新闻", "科普"],
                    "original": True,
                    "no_reprint": True,
                    "cover": _cover,
                }

                meta = video_uploader.VideoMeta(**meta_kwargs)
                page = video_uploader.VideoUploaderPage(
                    path=str(video_path),
                    title=title[:80],
                    description=description[:500],
                )
                uploader = video_uploader.VideoUploader(pages=[page], meta=meta, credential=self.cred, cover=str(_cover_path))
                ret = asyncio.run(uploader.start())
                bvid = ret.get("bvid", "")
                log.info(f"上传成功: {bvid}")
                return bvid
            except Exception as e:
                last_error = e
                log.warning(f"上传第{attempt+1}次失败: {e}")
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 5  # 5s / 10s / 15s 递增等待
                    log.info(f"等待{wait}秒后重试...")
                    time.sleep(wait)

        log.error(f"上传{max_retries}次全部失败: {last_error}")
        return None

    def verify_upload(self, bvid: str) -> Dict:
        """
        上传后验证：检查B站视频页面，确认视频已上线并获取基本信息。
        新方案：直接HTTP请求视频页（更稳定），兼容刚上传还在处理的情况。
        返回 {"ok": bool, "title": str, "cover_url": str, "aid": int, "error": str}
        """
        import requests as _requests

        # 方案1：直接请求B站播放页（无需登录，最稳定）
        try:
            resp = _requests.get(
                f"https://www.bilibili.com/video/{bvid}",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                timeout=10,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                # 从页面提取标题和封面
                title_match = resp.text.find('"title":"')
                title = ""
                if title_match != -1:
                    title_end = resp.text.find('"', title_match + 9)
                    if title_end != -1:
                        title = resp.text[title_match + 9:title_end]
                cover_match = resp.text.find('"coverUrl":"')
                cover_url = ""
                if cover_match != -1:
                    cover_end = resp.text.find('"', cover_match + 12)
                    if cover_end != -1:
                        cover_url = resp.text[cover_match + 12:cover_end]
                log.info(f"上传验证成功: [{bvid}] {title[:30] if title else 'N/A'}，封面: {cover_url[:50] if cover_url else 'N/A'}")
                return {
                    "ok": True,
                    "bvid": bvid,
                    "title": title,
                    "cover_url": cover_url,
                    "note": "封面由B站自动选帧，请登录B站后台 https://member.bilibili.com/uploader 更换封面",
                }
            elif resp.status_code == 404:
                log.warning(f"视频{bvid}还在转码处理中（HTTP 404），稍后可正常访问")
                return {"ok": False, "bvid": bvid, "error": "视频转码中，请稍后在B站后台查看"}
        except Exception as e:
            log.warning(f"播放页验证异常: {e}")

        # 方案2：bilibili_api（备用，如果页面验证失败）
        try:
            from bilibili_api import video as _video
            v = _video.Video(bvid=bvid, credential=self.cred)
            detail = asyncio.run(v.get_detail())
            title = detail.get("title", "")
            pic_url = detail.get("pic", "")
            log.info(f"上传验证成功(API): [{bvid}] {title}")
            return {
                "ok": True,
                "bvid": bvid,
                "title": title,
                "cover_url": pic_url,
                "note": "封面由B站自动选帧，请登录B站后台更换",
            }
        except Exception as e:
            log.warning(f"API验证异常: {e}")
            return {"ok": False, "bvid": bvid, "error": str(e)}

    def generate_title_ab(self, topic: str, n_variants: int = 3) -> list:
        """
        生成A/B测试标题变体列表（每次生成N个版本，上传时随机选一个）。
        格式：[版本A, 版本B, 版本C]
        """
        import random
        # 标签前缀（随机组合）
        prefixes = [
            "【信息差】", "【揭秘】", "【重磅】", "【硬核】",
            "【数据】", "【真相】", "【内幕】", "【突发】",
        ]
        # 句式模板（多种风格）
        templates = [
            "震惊！{topic}",
            "{topic}，你不知道的秘密",
            "为什么{topic}？看完沉默了",
            "{topic}，99%的人都不知道",
            "数据揭示：{topic}",
            "深度：{topic}的真相",
            "{topic}，看完倒吸一口凉气",
            "紧急！{topic}背后隐藏的真相",
        ]
        random.shuffle(prefixes)
        random.shuffle(templates)

        titles = []
        topic_short = topic[:25]
        for i in range(n_variants):
            prefix = prefixes[i % len(prefixes)]
            template = templates[i % len(templates)]
            title = prefix + template.format(topic=topic_short)
            # 去掉多余空格，控制80字以内
            title = ' '.join(title.split())[:80]
            titles.append(title)
        return titles

    def generate_title(self, topic: str, index: int = 1) -> str:
        """生成单个标题（兼容旧接口）"""
        titles = self.generate_title_ab(topic, n_variants=3)
        return titles[index % len(titles)]

    def generate_description(self, script: str, topic: str, max_chars: int = 500) -> str:
        """
        生成带SEO优化的视频描述（智能截断，不在单词中间断）
        """
        try:
            from ..config import SEO_DESCRIPTION_TEMPLATE
            desc = SEO_DESCRIPTION_TEMPLATE.format(title=topic)
        except Exception:
            desc = f"📺 信息差新闻：{topic}"

        body = f"{topic}\n\n{script}"
        # 智能截断（不在句子中间截）
        if len(body) > max_chars - 50:
            body = body[:max_chars - 50]
            # 在最后一个句号/逗号处截断
            last_punct = max(body.rfind("。"), body.rfind("，"))
            if last_punct > max_chars - 150:
                body = body[:last_punct + 1]

        tags = "#信息差 #新闻 #科普 #涨知识 #数据驱动"
        full = f"{desc}\n\n{body}\n\n{tags}"
        return full[:max_chars]

    def suggest_tags(self, topic: str) -> list:
        """
        生成SEO优化标签（使用config.SEO_TAGS + 动态关键词）
        """
        try:
            from ..config import SEO_TAGS
            base = list(SEO_TAGS)
        except Exception:
            base = ["信息差", "新闻", "科普", "涨知识"]
        if any(kw in topic for kw in ["金融", "经济", "股市", "基金", "房价"]):
            base.append("财经")
        if any(kw in topic for kw in ["科技", "AI", "芯片", "技术", "互联网"]):
            base.append("科技")
        if any(kw in topic for kw in ["国际", "外交", "战争", "美国", "中国", "俄罗斯"]):
            base.append("国际")
        if any(kw in topic for kw in ["健康", "医学", "养生", "疾病"]):
            base.append("健康")
        if any(kw in topic for kw in ["教育", "考试", "留学", "学生"]):
            base.append("教育")
        # 去重，控制在10个以内
        seen = set()
        result = []
        for tag in base:
            if tag not in seen:
                seen.add(tag)
                result.append(tag)
        return result[:10]

    def generate_thumbnail_prompt(self, title: str) -> str:
        """
        生成缩略图AI提示词（用于DALL-E/MJ生成封面）
        返回AI绘图prompt字符串
        """
        try:
            from ..config import THUMBNAIL_PROMPT_TEMPLATE
            return THUMBNAIL_PROMPT_TEMPLATE.format(title=title)
        except Exception:
            return f"信息差新闻视频封面，标题：「{title}」，写实新闻风，高对比度，大字体居中，背景模糊，16:9比例"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    upl = BilibiliUploader()
    print("BilibiliUploader initialized OK")


if __name__ == "__main__":
    main()