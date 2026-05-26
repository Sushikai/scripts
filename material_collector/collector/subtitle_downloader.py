#!/usr/bin/env python3
"""
yt-dlp 字幕采集模块
从 B站/抖音/YouTube 下载字幕/弹幕，作为分镜台词素材
"""

import json
import logging
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
SUBTITLES_DIR = PROJECT_ROOT / "materials" / "subtitles"


# ============ 字幕下载器 ============

class SubtitleDownloader:
    """用 yt-dlp 下载字幕，支持：B站（ass/srt）、抖音、YouTube"""

    def __init__(
        self,
        output_dir=None,
        cookies_path=None,
    ):
        if output_dir is None:
            output_dir = SUBTITLES_DIR
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_path = cookies_path

    def _run_yt_dlp(self, args, timeout=60):
        """执行 yt-dlp 命令"""
        cmd = ["yt-dlp"] + args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except FileNotFoundError:
            return -1, "", "yt-dlp not found"

    def download_bilibili_subs(self, bv_id):
        """
        下载B站视频字幕
        bv_id: B站视频BV号（如 BV1xx411c7mD）
        Returns: [{"type": "srt/ass", "path": "...", "text": "..."}]
        """
        url = f"https://www.bilibili.com/video/{bv_id}"
        output_template = str(self.output_dir / f"{bv_id}_%(lang)s.%(ext)s")

        args = [
            url,
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", "zh-Hans,zh,ai-zh",
            "--sub-format", "srt/ass/vtt",
            "-o", output_template,
            "--skip-download",
            "--quiet",
            "--no-warnings",
        ]

        if self.cookies_path and Path(self.cookies_path).exists():
            args += ["--cookies", str(self.cookies_path)]

        code, out, err = self._run_yt_dlp(args, timeout=90)

        results = []
        if code == 0:
            for ext in ["srt", "ass", "vtt"]:
                for f in self.output_dir.glob(f"{bv_id}_*.{ext}"):
                    text = self._parse_subtitle(f)
                    if text:
                        results.append({
                            "type": ext,
                            "path": str(f),
                            "text": text,
                            "bv_id": bv_id,
                            "url": url,
                            "timestamp": datetime.now().isoformat(),
                        })
            logger.info(f"[B站字幕] {bv_id}: 下载到 {len(results)} 个字幕文件")
        else:
            logger.warning(f"[B站字幕] {bv_id} 下载失败: {err[:100] if err else 'no error'}")

        return results

    def download_bilibili_danmu(self, bv_id):
        """下载B站弹幕"""
        cid = self._get_cid(bv_id)
        if not cid:
            return {}

        url = f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"
        output_path = self.output_dir / f"{bv_id}_danmu.xml"

        try:
            import requests
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200 and b"<d " in resp.content:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                danmu_list = self._parse_danmu(resp.content)
                logger.info(f"[B站弹幕] {bv_id}: {len(danmu_list)} 条弹幕")
                return {
                    "path": str(output_path),
                    "text": danmu_list,
                    "bv_id": bv_id,
                    "count": len(danmu_list),
                }
        except Exception as e:
            logger.warning(f"[B站弹幕] {bv_id} 失败: {e}")

        return {}

    def _get_cid(self, bv_id):
        """获取B站视频CID"""
        try:
            import requests
            resp = requests.get(
                f"https://api.bilibili.com/x/player/pagelist?bvid={bv_id}",
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == 0:
                return str(data["data"][0]["cid"])
        except:
            pass
        return None

    def _parse_subtitle(self, path):
        """解析字幕文件，返回纯文本"""
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            suffix = path.suffix.lower()
            if suffix == ".srt":
                return self._parse_srt(content)
            elif suffix == ".ass":
                return self._parse_ass(content)
            elif suffix == ".vtt":
                return self._parse_vtt(content)
            return content
        except Exception as e:
            logger.warning(f"解析字幕失败 {path}: {e}")
            return ""

    def _parse_srt(self, content):
        lines = []
        for line in content.split("\n"):
            line = line.strip()
            if line and not line.isdigit() and "-->" not in line:
                line = re.sub(r"<[^>]+>", "", line)
                if line:
                    lines.append(line)
        return " ".join(lines)

    def _parse_ass(self, content):
        lines = []
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) >= 10:
                    text = parts[9]
                    text = re.sub(r"\{[^}]+\}", "", text)
                    text = re.sub(r"\\N", " ", text)
                    text = re.sub(r"<[^>]+>", "", text)
                    if text.strip():
                        lines.append(text.strip())
        return " ".join(lines)

    def _parse_vtt(self, content):
        lines = []
        for line in content.split("\n"):
            line = line.strip()
            if line and "-->" not in line and not line.startswith("WEBVTT"):
                line = re.sub(r"<[^>]+>", "", line)
                if line:
                    lines.append(line)
        return " ".join(lines)

    def _parse_danmu(self, xml_bytes):
        try:
            text = xml_bytes.decode("utf-8")
            danmu = re.findall(r"<d[^>]*>([^<]+)</d>", text)
            return danmu
        except:
            return []

    def download_batch(self, bv_ids, include_danmu=True):
        """批量下载字幕"""
        all_subs = []
        all_danmu = []
        for bv in bv_ids:
            logger.info(f"下载字幕: {bv}")
            subs = self.download_bilibili_subs(bv)
            all_subs.extend(subs)
            if include_danmu:
                danmu = self.download_bilibili_danmu(bv)
                if danmu.get("text"):
                    all_danmu.append(danmu)
        return {"subtitles": all_subs, "danmu": all_danmu}


# ============ BV号提取 ============

class BVExtractor:
    """从文本/URL/数据库中提取BV号"""

    @staticmethod
    def extract_from_url(text):
        match = re.search(r"BV[a-zA-Z0-9]{10}", text)
        return match.group(0) if match else None

    @staticmethod
    def extract_from_text(text):
        return re.findall(r"BV[a-zA-Z0-9]{10}", text)

    @staticmethod
    def extract_from_db(db_path=None):
        if db_path is None:
            db_path = PROJECT_ROOT / "database" / "materials.db"
        import sqlite3
        bvs = set()
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT raw_text, source_url FROM materials")
            for row in cursor.fetchall():
                for t in row:
                    if t:
                        bvs.update(BVExtractor.extract_from_text(t or ""))
            conn.close()
        except Exception as e:
            logger.warning(f"提取BV号失败: {e}")
        return list(bvs)


# ============ 字幕存储 ============

def save_subtitles_to_db(subtitles, danmu, db_path=None):
    """保存字幕到数据库"""
    if db_path is None:
        db_path = PROJECT_ROOT / "database" / "materials.db"
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subtitles (
                id TEXT PRIMARY KEY,
                bv_id TEXT,
                platform TEXT DEFAULT 'bilibili',
                content_type TEXT,
                text TEXT,
                file_path TEXT,
                url TEXT,
                timestamp TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for sub in subtitles:
            cursor.execute("""
                INSERT OR IGNORE INTO subtitles
                (id, bv_id, platform, content_type, text, file_path, url, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                uuid.uuid4().hex[:12],
                sub.get("bv_id"),
                "bilibili",
                sub.get("type", "subtitle"),
                sub.get("text", ""),
                sub.get("path", ""),
                sub.get("url", ""),
                sub.get("timestamp", datetime.now().isoformat()),
            ))
        for dm in danmu:
            cursor.execute("""
                INSERT OR IGNORE INTO subtitles
                (id, bv_id, platform, content_type, text, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                uuid.uuid4().hex[:12],
                dm.get("bv_id"),
                "bilibili",
                "danmu",
                "\n".join(dm.get("text", [])),
                datetime.now().isoformat(),
            ))
        conn.commit()
        conn.close()
        logger.info(f"字幕入库: {len(subtitles)} 字幕, {len(danmu)} 弹幕")
        return True
    except Exception as e:
        logger.error(f"字幕入库失败: {e}")
        return False


# ---- 单元测试 ----
if __name__ == "__main__":
    import subprocess
    print("yt-dlp 字幕采集模块已加载")
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
        print(f"yt-dlp 版本: {result.stdout.strip()}")
    except FileNotFoundError:
        print("⚠️ yt-dlp 未安装: brew install yt-dlp")

    print()
    print("BV号提取示例:")
    test_text = "BV1xx411c7mD 和 BV1GJ411x7h2 这两个视频"
    bvs = BVExtractor.extract_from_text(test_text)
    print(f"  提取结果: {bvs}")
