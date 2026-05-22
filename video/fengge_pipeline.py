#!/usr/bin/env python3
"""
峰哥视频流水线：搜索 -> 下载 -> 裁剪90% -> 上传B站 -> 监控评论
每天12:00和18:00自动运行
"""

import requests
import subprocess
import json
import random
import sys
import time
import urllib.parse
from pathlib import Path

# ========== 配置 ==========
COOKIES = {
    "SESSDATA": "577e3116%2C1794848457%2C1661e%2A52CjD5ybsVR6H9X4F9cCN74F9w2gNdoVnSxOWky3IWFkRL5NUuT3I5aQVNAp6MpijkaN4SVjA5d2E5UGtfaXdoLVN5YTF0VEZMbU1jd0hCajNWYkpxam5OdW9QZXVLaHh3aUdjakg4czFyRDBqbXFBMExhMllvTDdtU0ZZVFZ4eV9QUG5NcWlIOWp3IIEC",
    "bili_jct": "fcd844961a4de0c0e1ebbbe05b183fc6",
    "buvid3": "3169493F-D668-AC48-4C96-6FB6DEFFF40E15104infoc",
    "buvid4": "9A4E1CC4-30BF-751C-0075-910E6C46849G47286-026051823-74Mos/+u6OM9VVAgAws/WQ%3D%3D",
    "buvid_fp": "4927fafa58d41d1530891c14ea4ea757",
    "CURRENT_FNVAL": "4048",
    "CURRENT_QUALITY": "120",
    "DedeUserID": "140289989",
    "DedeUserID__ckMd5": "d62d826182d027e2",
    "fingerprint": "4927fafa58d41d1530891c14ea4ea757",
    "sid": "drfqj8r1",
    "rpdid": "|(umuY~)Y~um0J'u~~YlYlRRk",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}
WORK_DIR = Path("/Users/kaikai/tiktok_automation/fengge_downloads")
UPLOAD_DIR = Path("/Users/kaikai/Desktop/峰哥成品待上传B站")
HISTORY_FILE = Path("/Users/kaikai/tiktok_automation/fengge_history.json")

# ========== 工具函数 ==========
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def load_history():
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return {}

def save_history(h):
    HISTORY_FILE.write_text(json.dumps(h, ensure_ascii=False, indent=2))

def get_search_results(keyword="峰哥", pages=3):
    """搜索B站视频，返回列表"""
    all_results = []
    keyword_enc = urllib.parse.quote(keyword)
    
    for page in range(1, pages + 1):
        url = f"https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={keyword_enc}&order=click&page={page}&pagesize=20"
        r = requests.get(url, headers=HEADERS, cookies=COOKIES, timeout=10)
        d = r.json()
        if d['code'] != 0:
            log(f"搜索失败: {d.get('message')}")
            break
        results = d['data']['result']
        for v in results:
            title = v['title'].replace('<em class="keyword">', '').replace('</em>', '')
            all_results.append({
                'bvid': v['bvid'],
                'title': title,
                'author': v.get('author', '未知'),
                'play': v.get('play_number', v.get('play', 0)),
            })
        log(f"第{page}页: 找到{len(results)}个")
        time.sleep(1)
    
    return all_results

def download_video(bvid, output_dir):
    """用yt-dlp下载视频到output_dir"""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{bvid}.mp4"
    
    if output_file.exists():
        log(f"视频已存在，跳过下载: {bvid}")
        return output_file
    
    cmd = [
        "yt-dlp",
        "--cookies-from-browser", "chrome",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", str(output_file),
        f"https://www.bilibili.com/video/{bvid}"
    ]
    
    log(f"下载: {bvid}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        log(f"下载完成: {output_file}")
        return output_file
    else:
        log(f"下载失败: {result.stderr[-200:]}")
        return None

def crop_to_90(input_file, output_file):
    """
    将视频画面缩放到90%，保留中间部分（上下左右各裁10%）
    FFmpeg: scale=0.9*iw:0.9*ih然后pad补边保持原尺寸
    """
    # 先获取原视频尺寸
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(input_file)],
        capture_output=True, text=True
    )
    info = json.loads(probe.stdout)
    streams = info['streams']
    if not streams:
        log("无法获取视频尺寸")
        return None
    w = streams[0]['width']
    h = streams[0]['height']
    log(f"原尺寸: {w}x{h}")
    
    # 计算裁剪后尺寸（90%）
    new_w = int(w * 0.9)
    new_h = int(h * 0.9)
    
    # 裁剪中间部分
    x_offset = (w - new_w) // 2
    y_offset = (h - new_h) // 2
    
    cmd = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-vf", f"crop={new_w}:{new_h}:{x_offset}:{y_offset}",
        "-c:a", "copy",
        str(output_file)
    ]
    
    log(f"裁剪: {w}x{h} -> {new_w}x{new_h} (偏移 x={x_offset}, y={y_offset})")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        log(f"裁剪完成: {output_file}")
        return output_file
    else:
        log(f"裁剪失败: {result.stderr[-200:]}")
        return None

def run_pipeline():
    """运行完整流水线"""
    log("=" * 50)
    log("峰哥视频流水线启动")
    
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. 搜索"峰哥"视频
    log("步骤1: 搜索峰哥视频...")
    results = get_search_results("峰哥", pages=2)
    if not results:
        log("搜索失败，退出")
        return
    
    # 2. 过滤一年前（2025年4月-5月）的视频，按播放量排序
    # 这里简化：选播放量最高的
    history = load_history()
    downloaded = set(history.keys())
    
    candidates = [v for v in results if v['bvid'] not in downloaded]
    log(f"候选视频: {len(candidates)}个（已排除{len(results)-len(candidates)}个历史下载）")
    
    if not candidates:
        log("没有新视频，退出")
        return
    
    # 按播放量排序，选前5个随机选1个（避免每次都下同一个）
    candidates.sort(key=lambda x: x.get('play', 0), reverse=True)
    top5 = candidates[:5]
    chosen = random.choice(top5)
    bvid = chosen['bvid']
    log(f"选中: {bvid} | {chosen['title']} | 播放:{chosen.get('play',0)}")
    
    # 3. 下载
    raw_file = download_video(bvid, WORK_DIR)
    if not raw_file:
        log("下载失败，退出")
        return
    
    # 4. 裁剪90%
    cropped_file = WORK_DIR / f"{bvid}_cropped.mp4"
    result = crop_to_90(raw_file, cropped_file)
    if not result:
        log("裁剪失败，退出")
        return
    
    # 5. 移动到上传目录
    upload_file = UPLOAD_DIR / cropped_file.name
    import shutil
    shutil.move(str(cropped_file), str(upload_file))
    log(f"已移动到上传目录: {upload_file}")
    
    # 6. 记录历史
    history[bvid] = {
        'title': chosen['title'],
        'downloaded_at': time.strftime('%Y-%m-%d %H:%M'),
        'file': str(upload_file),
        'cropped_from': str(raw_file)
    }
    save_history(history)
    
    # 7. 打印结果
    log("=" * 50)
    log(f"✅ 流水线完成!")
    log(f"视频: {chosen['title']}")
    log(f"文件: {upload_file}")
    log(f"下一步: 用biliup上传到B站")

if __name__ == "__main__":
    run_pipeline()
