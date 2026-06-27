"""Hand-crafted bar sequences that pin down the ORB entry/exit logic."""

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from orb.config import StrategyConfig, ES
from orb.backtest import run_backtest


ET = ZoneInfo("America/New_York")


def _bars(rows):
    idx = [dt.datetime.combine(dt.date(2024, 1, 2), dt.time(h, m), ET) for h, m, *_ in rows]
    data = [{"open": o, "high": hi, "low": lo, "close": c} for _, _, o, hi, lo, c in rows]
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx))


def _minute_series(or_high, or_low, breakout_to, n_after=40):
    """Build a day: a 30-min OR, then a clean move to `breakout_to`."""
    rows = []
    # opening range: oscillate between or_low and or_high for 30 minutes
    for i in range(30):
        h, m = 9, 30 + i
        o = or_low if i % 2 else or_high
        rows.append((h, m, o, or_high, or_low, o))
    # after OR: drift straight toward breakout_to
    start = or_high if breakout_to > or_high else or_low
    for i in range(n_after):
        minute = 30 + i
        h = 10 + minute // 60
        m = minute % 60
        frac = (i + 1) / n_after
        px = start + (breakout_to - start) * frac
        rows.append((h, m, px, px + 1, px - 1, px))
    return _bars(rows)


def test_long_breakout_hits_target():
    # OR 4800-4810 (40 ticks). Range stop, 1R target -> long target ~4820.
    df = _minute_series(or_high=4810, or_low=4800, breakout_to=4825)
    cfg = StrategyConfig(name="t", direction="long", stop_type="range",
                         target_type="r_multiple", r_multiple=1.0,
                         slippage_ticks=0, commission_per_side=0, entry_buffer_ticks=1)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "long"
    assert t.exit_reason == "target"
    assert t.pnl_usd > 0


def test_short_breakout_hits_target():
    df = _minute_series(or_high=4810, or_low=4800, breakout_to=4780)
    cfg = StrategyConfig(name="t", direction="short", stop_type="range",
                         target_type="r_multiple", r_multiple=1.0,
                         slippage_ticks=0, commission_per_side=0, entry_buffer_ticks=1)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    assert trades[0].direction == "short"
    assert trades[0].exit_reason == "target"
    assert trades[0].pnl_usd > 0


def test_no_breakout_no_trade():
    # Build a day whose price never leaves the OR (4800-4810) after the open.
    rows = []
    for i in range(30):  # opening range
        h, m = 9, 30 + i
        o = 4800 if i % 2 else 4810
        rows.append((h, m, o, 4810, 4800, o))
    for i in range(60):  # after OR: oscillate strictly inside the range
        minute = 30 + i
        h = 10 + minute // 60
        m = minute % 60
        px = 4805 if i % 2 else 4806
        rows.append((h, m, px, 4808, 4802, px))  # high/low stay inside 4800-4810
    df = _bars(rows)
    cfg = StrategyConfig(name="t", direction="both", entry_buffer_ticks=2,
                         slippage_ticks=0, commission_per_side=0)
    trades = run_backtest(df, cfg)
    assert trades == []


def test_max_one_trade_per_day():
    df = _minute_series(or_high=4810, or_low=4800, breakout_to=4830)
    cfg = StrategyConfig(name="t", max_trades_per_day=1, slippage_ticks=0,
                         commission_per_side=0)
    trades = run_backtest(df, cfg)
    assert len(trades) <= 1


if __name__ == "__main__":
    test_long_breakout_hits_target()
    test_short_breakout_hits_target()
    test_no_breakout_no_trade()
    test_max_one_trade_per_day()
    print("all tests passed")
