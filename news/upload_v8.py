#!/usr/bin/env python3
"""Standalone upload script for the generated video"""
import asyncio, json, sys, subprocess, os
from pathlib import Path

video_path = "/Users/kaikai/ai_video_project/news_outputs/【20岁还没开始环球旅行】2026年6月7日信息差_473114d8.mp4"
print(f"Video: {os.path.getsize(video_path)//1024//1024}MB")

p = Path("/Users/kaikai/scripts/20岁还没开始环球旅行_cookies.txt")
cookies = json.loads(p.read_text())

import bilibili_api
from bilibili_api.clients.HTTPXClient import HTTPXClient
from bilibili_api import video_uploader, Credential, Picture

bilibili_api.register_client("httpx", HTTPXClient)
bilibili_api.select_client("httpx")

cred = Credential(
    sessdata=cookies["SESSDATA"],
    bili_jct=cookies["bili_jct"],
    buvid3=cookies["buvid3"],
)

cover_path = "/tmp/cover_v8.jpg"
r = subprocess.run(["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01", "-vframes", "1", "-q:v", "2", cover_path], capture_output=True, timeout=15)
print(f"Cover: {r.returncode}")

date_str = "2026年6月7日"
actual_count = 8
title = "【晚差信息差】2026.6.7：70%民众反对AI…今日热点速递"
desc = f"""📰 今日信息差日报 | {date_str} | {actual_count}条热点

70%民众反对AI，美国人希望美国输掉人工智能战争, Anthropic全球警告，OpenAI已跨"可靠性阈值"：AI自我加速启动, 大厂"电子吧唧"，"手表边角料"收割二次元？, Delays to defence plan undermine UK credibility MPs say, Starmer tells supporters he will fight any leadership contest, Cosmeticorexia: How girls are falling down a skincare rabbit hole, Police officer turned Love Island US contestant faces hometown backlash, Hegseth attacks Europe over migration with beach invasion D-Day speech
…（更多热点见视频）

#信息差 #新闻汇总 #每日热点 #2026"""

tags = ["信息差", "新闻汇总", "每日热点", "2026"]

async def upload():
    page = video_uploader.VideoUploaderPage(path=video_path, title=title, description=desc)
    cover = Picture.from_file(cover_path)
    meta = video_uploader.VideoMeta(
        tid=201, title=title, desc=desc, cover=cover, tags=tags,
        original=True, source="网络", no_reprint=True,
        up_close_danmu=False, up_close_reply=False,
    )
    uploader = video_uploader.VideoUploader(pages=[page], meta=meta, credential=cred)
    print("Uploading...")
    sys.stdout.flush()
    ret = await uploader.start()
    print(f"Result: {ret}")
    return ret

result = asyncio.run(upload())
print(f"Done! bvid={result}")
sys.stdout.flush()