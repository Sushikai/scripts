from __future__ import annotations

from tuixue_v3.web.server import _filter_intraday_ticks_for_date


def test_filter_intraday_ticks_keeps_exact_date_and_strips_date_prefix():
    ticks = [
        {"time": "2026-07-22 09:30:00", "price": 10.0},
        {"time": "2026-07-22 09:31:00", "price": 10.1},
    ]

    filtered, actual_dates = _filter_intraday_ticks_for_date(ticks, "20260722", allow_time_only=False)

    assert [tick["time"] for tick in filtered] == ["09:30:00", "09:31:00"]
    assert actual_dates == {"20260722"}


def test_filter_intraday_ticks_rejects_wrong_and_mixed_dates():
    ticks = [
        {"time": "2026-07-21 09:30:00", "price": 9.8},
        {"time": "2026-07-22 09:31:00", "price": 10.1},
    ]

    filtered, actual_dates = _filter_intraday_ticks_for_date(ticks, "20260722", allow_time_only=False)

    assert [tick["time"] for tick in filtered] == ["09:31:00"]
    assert actual_dates == {"20260721", "20260722"}


def test_filter_intraday_ticks_rejects_unverifiable_historical_times():
    ticks = [{"time": "09:30:00", "price": 10.0}]

    filtered, actual_dates = _filter_intraday_ticks_for_date(ticks, "20260722", allow_time_only=False)

    assert filtered == []
    assert actual_dates == set()


def test_filter_intraday_ticks_allows_time_only_for_today_source():
    ticks = [{"time": "09:30:00", "price": 10.0}]

    filtered, actual_dates = _filter_intraday_ticks_for_date(ticks, "20260722", allow_time_only=True)

    assert filtered == ticks
    assert actual_dates == set()


def test_filter_intraday_ticks_handles_compact_tencent_timestamp():
    ticks = [
        {"time": "202607220930", "price": 10.0},
        {"time": "202607210931", "price": 9.9},
    ]

    filtered, actual_dates = _filter_intraday_ticks_for_date(ticks, "20260722", allow_time_only=False)

    assert [tick["time"] for tick in filtered] == ["09:30:00"]
    assert actual_dates == {"20260721", "20260722"}
