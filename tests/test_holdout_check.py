"""Tests for the freeze-period count checker: P&L must be hard-suppressed —
the report may contain counts and skip reasons, never a dollar figure."""

import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from orb.backtest import Trade
from holdout_count_check import (FROZEN_PICKS, HOLDOUT_START, TARGET_DECIDED,
                                 build_report, count_summary)


def T(day: str, pnl: float) -> Trade:
    d = dt.date.fromisoformat(day)
    t0 = dt.datetime.combine(d, dt.time(10, 0))
    return Trade(day=d, direction="long", entry_time=t0, entry_price=23456.75,
                 exit_time=t0, exit_price=23470.25,
                 exit_reason="target" if pnl > 0 else ("stop" if pnl < 0 else "close"),
                 risk_points=10.0, pnl_points=pnl / 20.0, pnl_usd=pnl, r_multiple=0.5)


def _fake_run(n_july, n_aug, with_due=False):
    trades = [T("2026-06-15", 1234.56)]                       # warmup: must vanish
    trades += [T("2026-07-10", 987.65)] * n_july
    trades += [T("2026-08-05", -432.10)] * n_aug
    trades += [T("2026-08-06", 0.0)] * 3                      # scratches: not decided
    counts = {name: count_summary(trades) for name in FROZEN_PICKS}
    skips = {name: {"no_session_bars": 9, "regime_filter": 21} for name in FROZEN_PICKS}
    return build_report(dt.date(2026, 8, 31), counts, skips)


def test_report_contains_no_dollar_figures():
    out = _fake_run(12, 9)
    assert "$" not in out
    assert not re.search(r"(?i)pnl|net|profit|usd", out)
    # prices from the Trade objects must not leak either
    assert "23456" not in out and "987" not in out and "1234" not in out


def test_counts_and_progress_are_right():
    out = _fake_run(12, 9)
    assert "'2026-07': 12" in out and "'2026-08': 9" in out
    assert f"cumulative decided: 21/{TARGET_DECIDED}" in out
    assert "19 more decided trades needed" in out
    assert "regime_filter" in out                              # skip decomposition present
    assert "2026-06" not in out                                # warmup trade suppressed


def test_scratches_do_not_count_and_due_notice_fires():
    out = _fake_run(25, 15)
    assert f"cumulative decided: 40/{TARGET_DECIDED}" in out   # 3 scratches excluded
    assert "EVALUATION DUE" in out
    assert "Do NOT evaluate with this tool" in out


def test_suppression_guard_trips_on_dollar_leak():
    counts = {name: {"per_month": {"2026-07 ($500)": 1}, "decided_total": 1}
              for name in FROZEN_PICKS}
    skips = {name: {} for name in FROZEN_PICKS}
    try:
        build_report(dt.date(2026, 7, 31), counts, skips)
        raise AssertionError("guard failed to trip on a $ leak")
    except RuntimeError:
        pass


TESTS = [
    test_report_contains_no_dollar_figures,
    test_counts_and_progress_are_right,
    test_scratches_do_not_count_and_due_notice_fires,
    test_suppression_guard_trips_on_dollar_leak,
]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"all {len(TESTS)} holdout-check tests passed")
