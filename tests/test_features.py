"""Unit tests for the round-2 entry filters (research/ROUND2_PREREG.md §2):
session-VWAP alignment, OR-size-vs-median regime filter, ATR-percentile filter.
All hand-crafted bar sequences — no market data involved.
"""

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from orb.config import StrategyConfig
from orb.backtest import run_backtest

ET = ZoneInfo("America/New_York")


def _cfg(**kw):
    base = dict(name="t", slippage_ticks=0, commission_per_side=0, entry_buffer_ticks=1,
                stop_type="range", target_type="r_multiple", r_multiple=3.0)
    base.update(kw)
    return StrategyConfig(**base)


def _day_rows(date: dt.date, or_low: float, or_high: float, after,
              even_vol: float = 100.0, odd_vol: float = 100.0):
    """One session: 30 OR bars oscillating or_low/or_high, then explicit
    (open, high, low, close[, volume]) bars from 10:00."""
    rows = []
    for i in range(30):
        t = dt.datetime.combine(date, dt.time(9, 30 + i), ET)
        if i % 2 == 0:
            rows.append((t, or_high, or_high, or_high - 0.25, or_high, even_vol))
        else:
            rows.append((t, or_low, or_low + 0.25, or_low, or_low, odd_vol))
    for i, spec in enumerate(after):
        t = dt.datetime.combine(date, dt.time(10, 0), ET) + dt.timedelta(minutes=i)
        o, h, l, c = spec[:4]
        v = spec[4] if len(spec) > 4 else 100.0
        rows.append((t, o, h, l, c, v))
    return rows


def _frame(rows) -> pd.DataFrame:
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.DataFrame(
        {"open": [r[1] for r in rows], "high": [r[2] for r in rows],
         "low": [r[3] for r in rows], "close": [r[4] for r in rows],
         "volume": [r[5] for r in rows]},
        index=idx,
    )


# --------------------------------------------------------------------------
# VWAP filter
# --------------------------------------------------------------------------

def _vwap_day(after):
    """OR 4800-4810 with volume loaded at the TOP: VWAP ~4809.8, while the
    last OR bar closes at 4800 — so prev-close < VWAP: longs blocked, shorts allowed."""
    return _frame(_day_rows(dt.date(2024, 1, 2), 4800, 4810, after,
                            even_vol=1000.0, odd_vol=10.0))


def test_vwap_blocks_counter_trend_long():
    # Price pokes above the OR high while trading below VWAP -> no long.
    df = _vwap_day([(4805, 4811, 4804, 4805), (4805, 4806, 4804, 4805)])
    assert run_backtest(df, _cfg(vwap_filter=True)) == []
    control = run_backtest(df, _cfg())
    assert len(control) == 1 and control[0].direction == "long"


def test_vwap_allows_aligned_short():
    # The same below-VWAP tape breaking BOTH sides: only the short may fire.
    df = _vwap_day([(4805, 4811, 4795, 4796), (4796, 4797, 4790, 4791)])
    trades = run_backtest(df, _cfg(vwap_filter=True))
    assert len(trades) == 1 and trades[0].direction == "short"
    # without the filter the both-trigger bar resolves long (tie goes to long)
    control = run_backtest(df, _cfg())
    assert len(control) == 1 and control[0].direction == "long"


def test_vwap_requires_volume_column():
    df = _vwap_day([(4805, 4811, 4804, 4805), (4805, 4806, 4804, 4805)]).drop(columns=["volume"])
    try:
        run_backtest(df, _cfg(vwap_filter=True))
        raise AssertionError("expected ValueError without a volume column")
    except ValueError:
        pass


# --------------------------------------------------------------------------
# OR-size vs trailing-median filter
# --------------------------------------------------------------------------

def _breakout_after(or_high, n=30):
    """Post-OR path that cleanly breaks the long trigger."""
    out = []
    for i in range(n):
        px = or_high + 1 + i * 0.5
        out.append((px, px + 1, px - 1, px))
    return out


def _flat_after(mid, n=30):
    return [(mid, mid + 0.5, mid - 0.5, mid)] * n


def test_or_median_filter():
    rows = []
    day0 = dt.date(2024, 1, 1)
    # days 1..21: OR size 10 (4800-4810), all with a breakout
    for i in range(21):
        rows += _day_rows(day0 + dt.timedelta(days=i), 4800, 4810, _breakout_after(4810))
    # day 22: OR size 4 (4806-4810), breakout — must be blocked (4 < median 10)
    rows += _day_rows(day0 + dt.timedelta(days=21), 4806, 4810, _breakout_after(4810))
    df = _frame(rows)

    stats: dict = {}
    trades = run_backtest(df, _cfg(min_or_vs_median=1.0, or_median_days=20), stats=stats)
    # days 1-20 lack history, day 22 is too small: only day 21 trades
    assert [t.day for t in trades] == [day0 + dt.timedelta(days=20)]
    assert stats["skipped"]["regime_filter"] == 21

    control = run_backtest(df, _cfg())
    assert len(control) == 22


# --------------------------------------------------------------------------
# ATR percentile filter
# --------------------------------------------------------------------------

def test_atr_percentile_filter():
    rows = []
    day0 = dt.date(2024, 1, 1)
    d = 0
    # 33 base days alternating session range 10 / 30 (flat after the OR)
    for i in range(33):
        lo, hi = (4800, 4810) if i % 2 == 0 else (4790, 4820)
        rows += _day_rows(day0 + dt.timedelta(days=d), lo, hi, _flat_after((lo + hi) / 2)); d += 1
    # 3 volatility-spike days: range 40
    for _ in range(3):
        rows += _day_rows(day0 + dt.timedelta(days=d), 4790, 4830, _flat_after(4810)); d += 1
    # test day A after the spike: prior ATR is the max of its window -> trades
    day_a = day0 + dt.timedelta(days=d)
    rows += _day_rows(day_a, 4800, 4810, _breakout_after(4810)); d += 1
    # 3 dead days: range 4
    for _ in range(3):
        rows += _day_rows(day0 + dt.timedelta(days=d), 4806, 4810, _flat_after(4808)); d += 1
    # test day B after the collapse: prior ATR is near the window bottom -> blocked
    day_b = day0 + dt.timedelta(days=d)
    rows += _day_rows(day_b, 4800, 4810, _breakout_after(4810)); d += 1
    df = _frame(rows)

    cfg = _cfg(min_atr_percentile=80.0, atr_days=3, atr_lookback=10)
    trades = run_backtest(df, cfg)
    assert [t.day for t in trades] == [day_a], [t.day for t in trades]

    control = run_backtest(df, _cfg())
    assert [t.day for t in control] == [day_a, day_b]


# --------------------------------------------------------------------------
# live-engine guard: cross-day filters must refuse until parity support exists
# --------------------------------------------------------------------------

def test_engine_refuses_cross_day_filters():
    from orb.live import OrbSessionEngine
    for kw in (dict(min_or_vs_median=1.0), dict(min_atr_percentile=50.0)):
        try:
            OrbSessionEngine(_cfg(**kw))
            raise AssertionError(f"engine accepted unsupported filter {kw}")
        except NotImplementedError:
            pass


TESTS = [
    test_vwap_blocks_counter_trend_long,
    test_vwap_allows_aligned_short,
    test_vwap_requires_volume_column,
    test_or_median_filter,
    test_atr_percentile_filter,
    test_engine_refuses_cross_day_filters,
]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"all {len(TESTS)} feature tests passed")
