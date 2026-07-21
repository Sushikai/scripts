"""
tuixue_v3 - 退学炒股 4 层递进式选股系统

公开 API:
  - run_stock_screen()        手动触发选股（顶层入口）
  - run_backtest()            回测（指定日期范围）
  - run_optimize()            网格扫描调优
  - push_to_telegram()        推送结果到 TG
"""
__all__ = [
    "run_stock_screen",
    "run_backtest",
    "push_to_telegram",
    "push_backtest_report",
]


def __getattr__(name):
    """懒加载：避免循环引用 + 单独模块缺失时仍可用"""
    if name in ("run_stock_screen", "run_backtest"):
        from . import screen
        return getattr(screen, name)
    if name in ("push_to_telegram", "push_backtest_report"):
        from . import telegram_push
        return getattr(telegram_push, name)
    raise AttributeError(name)