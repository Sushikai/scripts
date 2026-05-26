#!/usr/bin/env python3
"""
主入口 - 素材采集系统
支持：ADB模拟器采集 + Playwright网页采集

用法:
    python main.py --platform bilibili_web -k "宝宝可爱" --scroll 10
    python main.py --platform douyin_web -k "萌娃" --scroll 10
    python main.py --platform all_web -k "火花宝宝" --style "火花宝宝"
    python main.py --process-only
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from collector.adb_controller import ADBController, ADBError
from processor.material_processor import MaterialProcessor
from database.materials_db import MaterialDatabase


# ============ 日志配置 ============

def setup_logging(log_file: Path = None):
    """配置日志"""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_file or log_dir / f"collector_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger(__name__)


# ============ 命令行参数 ============

def parse_args():
    parser = argparse.ArgumentParser(
        description="短视频素材自动采集系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # Web采集（无需模拟器）
  python main.py -p bilibili_web -k "宝宝可爱" -k "萌娃" --scroll 10

  # Web采集B站
  python main.py -p douyin_web -k "火花宝宝" --scroll 10

  # 全Web平台采集
  python main.py -p all_web -k "火花宝宝" -k "不存在的小镇"

  # 模拟器采集（需要MuMu ADB）
  python main.py -p bilibili -k "宝宝" --scroll 20

  # 仅处理已采集素材
  python main.py --process-only
        """,
    )

    # 平台：支持模拟器版 + Web版
    parser.add_argument("-p", "--platform", default="bilibili_web",
                        choices=[
                            "douyin", "bilibili", "xiaohongshu", "all",   # 模拟器
                            "douyin_web", "bilibili_web", "all_web",         # Web
                        ],
                        help="采集平台 (默认: bilibili_web)")
    parser.add_argument("-k", "--keyword", action="append", dest="keywords", default=[],
                        help="搜索关键词 (可多次使用)")
    parser.add_argument("-s", "--style",
                        choices=["火花宝宝", "不存在的小镇", "通用"],
                        default="通用",
                        help="内容风格 (默认: 通用)")
    parser.add_argument("-d", "--duration", type=int, default=3600,
                        help="最大运行时长（秒），默认3600")
    parser.add_argument("--scroll", type=int, default=10,
                        help="滚动次数（默认: 10）")
    parser.add_argument("--max-per-keyword", type=int, default=30,
                        help="每个关键词最大采集条数（默认: 30）")
    parser.add_argument("--adb-host", default="127.0.0.1",
                        help="ADB 主机地址（默认: 127.0.0.1）")
    parser.add_argument("--adb-port", type=int, default=16384,
                        help="ADB 端口（默认: 16384）")
    parser.add_argument("--output", default="materials/raw",
                        help="原始素材输出目录")
    parser.add_argument("--ocr-engine", default="paddle",
                        choices=["paddle", "baidu"],
                        help="OCR 引擎（默认: paddle）")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="Playwright 无头模式（默认: True）")
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="关闭无头模式，显示浏览器窗口")
    parser.add_argument("--process-only", action="store_true",
                        help="仅处理已采集素材，不采集新内容")
    parser.add_argument("--no-process", action="store_true",
                        help="仅采集，不处理")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="详细输出")

    return parser.parse_args()


# ============ 采集器工厂 ============

def _is_web_platform(platform: str) -> bool:
    return platform.endswith("_web") or platform == "all_web"


def _get_native_platform(web_platform: str) -> str:
    """Web平台 -> 模拟器平台"""
    mapping = {
        "douyin_web": "douyin",
        "bilibili_web": "bilibili",
        "all_web": "all",
    }
    return mapping.get(web_platform, web_platform)


def run_web_collection(args, logger) -> bool:
    """Playwright 网页采集"""
    from collector.playwright_collector import create_web_collector
    import asyncio

    logger.info(f"=" * 60)
    logger.info(f"Web采集 | 平台: {args.platform} | 关键词: {args.keywords}")
    logger.info(f"=" * 60)

    start_time = time.time()
    db = MaterialDatabase()

    # 确定要采集的平台列表
    platform_map = {
        "douyin_web": ["douyin_web"],
        "bilibili_web": ["bilibili_web"],
        "all_web": ["douyin_web", "bilibili_web"],
    }
    platforms = platform_map.get(args.platform, [args.platform])

    keywords = args.keywords or ["宝宝可爱", "萌娃", "火花宝宝"]
    total_collected = 0

    for p in platforms:
        logger.info(f"\n>>> 开始采集: {p}")

        try:
            collector = create_web_collector(
                platform=p,
                output_dir=args.output,
                max_per_keyword=args.max_per_keyword,
                headless=args.headless,
            )

            items = collector.run(keywords=keywords, auto_scroll=args.scroll)

            for item in items:
                db.insert_material({
                    "id": item.id,
                    "platform": item.platform,
                    "keyword": item.keyword,
                    "content_type": item.content_type,
                    "raw_text": item.raw_text,
                    "video_title": item.video_title,
                    "video_bvid": "",
                    "timestamp": item.timestamp,
                    "source_url": item.video_url,
                    "ad_score": item.ad_score,
                    "hash": item.hash,
                })

            total_collected += len(items)
            logger.info(f"[{p}] 采集完成: {len(items)} 条")

        except Exception as e:
            logger.error(f"[{p}] 采集失败: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()

    elapsed = time.time() - start_time
    logger.info(f"\n采集完成: {total_collected} 条 | 耗时: {elapsed:.1f}秒")
    return True


def run_adb_collection(args, logger) -> bool:
    """ADB 模拟器采集"""
    from collector.collector_core import create_collector

    logger.info(f"=" * 60)
    logger.info(f"ADB采集 | 平台: {args.platform} | 关键词: {args.keywords}")
    logger.info(f"=" * 60)

    start_time = time.time()
    db = MaterialDatabase()

    try:
        collector = create_collector(
            platform=args.platform,
            adb_host=args.adb_host,
            adb_port=args.adb_port,
            output_dir=args.output,
            max_per_keyword=args.max_per_keyword,
            engine=args.ocr_engine,
        )
    except ADBError as e:
        logger.error(f"设备连接失败: {e}")
        return False

    keywords = args.keywords or ["宝宝可爱", "萌娃日常", "火花宝宝"]
    collected = collector.run(keywords=keywords, auto_scroll=args.scroll)

    for item in collected:
        db.insert_material(item)

    elapsed = time.time() - start_time
    logger.info(f"采集完成: {len(collected)} 条 | 耗时: {elapsed:.1f}秒")
    return True


# ============ AI 处理 ============

def run_processing(args, logger) -> bool:
    """执行 AI 处理"""
    logger.info(f"\n{'=' * 60}")
    logger.info(f"开始 AI 处理")
    logger.info(f"{'=' * 60}")

    db = MaterialDatabase()
    processor = MaterialProcessor(
        chroma_path=str(PROJECT_ROOT / "database" / "chroma_db"),
    )

    materials = db.get_unprocessed_materials(limit=100)
    logger.info(f"待处理: {len(materials)} 条")

    if not materials:
        logger.info("无待处理素材")
        return True

    processed_results = processor.process_batch(materials, workers=8)

    for orig, result in zip(materials, processed_results):
        if result.get("usable", False):
            db.insert_processed({
                "id": result.get("id"),
                "original_id": orig.get("id"),
                "platform": orig.get("platform"),
                "keyword": orig.get("keyword"),
                "clean_text": result.get("clean_text", ""),
                "category": result.get("category", "通用"),
                "mood": result.get("mood", ""),
                "tags": result.get("tags", []),
                "usable": result.get("usable", True),
                "reason": result.get("reason", ""),
                "suggestion": result.get("suggestion", ""),
                "vector_id": result.get("vector_id", ""),
            })
            db.mark_processed(orig.get("id"))

    db.update_statistics()
    stats = db.get_category_distribution()
    logger.info(f"分类统计: {stats}")

    output_path = PROJECT_ROOT / "materials" / "processed" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    processor.export_to_json(processed_results, output_path)

    logger.info(f"AI 处理完成，已处理 {len(processed_results)} 条")
    return True


# ============ 主函数 ============

def main():
    args = parse_args()

    logger = setup_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("短视频素材采集系统 v1.0")
    logger.info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"平台: {args.platform} ({'Web' if _is_web_platform(args.platform) else 'ADB模拟器'})")
    logger.info(f"关键词: {args.keywords or '(默认)'}")
    logger.info(f"风格: {args.style}")
    logger.info(f"滚动次数: {args.scroll}")
    logger.info("=" * 60)

    # 采集
    if not args.process_only:
        if _is_web_platform(args.platform):
            run_web_collection(args, logger)
        else:
            # 检查 ADB 是否可用（模拟器模式）
            try:
                adb = ADBController(host=args.adb_host, port=args.adb_port)
                logger.info(f"✅ ADB设备已连接: {adb.serial}")
            except ADBError as e:
                logger.error(f"⚠️ ADB设备未连接: {e}")
                logger.error("请使用 --platform bilibili_web 等 Web 模式，无需模拟器")
                logger.error("或确保 MuMu Player Pro 已启动并开启 ADB")
                sys.exit(1)
            run_adb_collection(args, logger)

    # 处理
    if not args.no_process:
        run_processing(args, logger)

    logger.info("\n" + "=" * 60)
    logger.info("全部完成!")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
