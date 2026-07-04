"""Unit tests for round-3 (research/ROUND3_PREREG.md): NQ cost math,
follow-through entries, band regime filter, time exits, benchmark-alpha
gate criterion, and the live-engine refusal of round-3 configs.
All strategy tests use hand-built bar sequences.
"""

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from orb.config import ES, MNQ, NQ, StrategyConfig
from orb.backtest import _close, run_backtest
from orb.metrics import profit_gate

ET = ZoneInfo("America/New_York")


def _cfg(**kw):
    base = dict(name="t", slippage_ticks=0, commission_per_side=0, entry_buffer_ticks=1,
                stop_type="range", target_type="r_multiple", r_multiple=5.0)
    base.update(kw)
    return StrategyConfig(**base)


def _day_rows(date, or_low, or_high, after):
    rows = []
    for i in range(30):
        t = dt.datetime.combine(date, dt.time(9, 30 + i), ET)
        px = or_high if i % 2 == 0 else or_low
        rows.append((t, px, or_high, or_low, px, 100.0))
    for i, spec in enumerate(after):
        t = dt.datetime.combine(date, dt.time(10, 0), ET) + dt.timedelta(minutes=i)
        o, h, l, c = spec[:4]
        rows.append((t, o, h, l, c, 100.0))
    return rows


def _frame(rows):
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.DataFrame(
        {"open": [r[1] for r in rows], "high": [r[2] for r in rows],
         "low": [r[3] for r in rows], "close": [r[4] for r in rows],
         "volume": [r[5] for r in rows]}, index=idx)


def _one_day(after):
    return _frame(_day_rows(dt.date(2024, 1, 2), 4800, 4810, after))


# --------------------------------------------------------------------------
# NQ cost math (prereg part 1: lock the spec numbers)
# --------------------------------------------------------------------------

def test_nq_cost_math():
    assert NQ.tick_size == 0.25 and NQ.point_value == 20.0
    assert NQ.tick_value == 5.0                      # 0.25 x $20
    assert NQ.commission_per_side == 2.45            # IBKR all-in, 2026-07
    t0 = dt.datetime(2024, 1, 2, 10, 0, tzinfo=ET)
    pos = {"direction": "long", "entry_time": t0, "entry_price": 20000.0,
           "stop": 19990.0, "target": 20020.0, "risk": 10.0, "day": t0.date()}
    cfg = StrategyConfig(commission_per_side=NQ.commission_per_side, contracts=1)
    tr = _close(pos, t0, 20010.0, "target", cfg, NQ)
    assert abs(tr.pnl_usd - (10.0 * 20.0 - 2 * 2.45)) < 1e-9   # $195.10
    cfg_m = StrategyConfig(commission_per_side=MNQ.commission_per_side, contracts=1)
    tr_m = _close(dict(pos), t0, 20010.0, "target", cfg_m, MNQ)
    assert abs(tr_m.pnl_usd - (10.0 * 2.0 - 2 * 0.62)) < 1e-9  # $18.76


# --------------------------------------------------------------------------
# H1 — follow-through entries
# --------------------------------------------------------------------------

def test_touch_without_close_does_not_enter():
    after = [(4805, 4811, 4804, 4805)] + [(4805, 4806, 4804, 4805)] * 5
    df = _one_day(after)
    assert len(run_backtest(df, _cfg())) == 1                      # first-touch control
    assert run_backtest(df, _cfg(confirm_closes=2)) == []          # never confirmed


def test_confirm_closes_enters_next_open():
    after = [
        (4805, 4811, 4804, 4811),    # close 1 beyond
        (4811, 4812, 4810.5, 4812),  # close 2 beyond -> confirmed
        (4813, 4814, 4812, 4813),    # market order fills HERE at the open
        (4813, 4814, 4812, 4813),
    ]
    trades = run_backtest(_one_day(after), _cfg(confirm_closes=2))
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "long"
    assert t.entry_price == 4813.0                       # next bar's OPEN, not the trigger
    assert t.entry_time.time() == dt.time(10, 2)


def test_close_back_inside_resets_count():
    after = [
        (4805, 4811, 4804, 4811),    # beyond (1)
        (4811, 4812, 4805, 4806),    # back inside -> reset
        (4806, 4812, 4805, 4811),    # beyond (1)
        (4811, 4813, 4810.5, 4812),  # beyond (2) -> confirmed
        (4812, 4813, 4811, 4812),    # entry at THIS open
        (4812, 4813, 4811, 4812),
    ]
    trades = run_backtest(_one_day(after), _cfg(confirm_closes=2))
    assert len(trades) == 1
    assert trades[0].entry_time.time() == dt.time(10, 4)


def test_confirm_beyond_ticks():
    # 8 ticks = 2 points beyond the 4810 OR high -> needs a close >= 4812
    near = [(4805, 4811, 4804, 4811.5)] + [(4805, 4806, 4804, 4805)] * 4
    assert run_backtest(_one_day(near), _cfg(confirm_beyond_ticks=8)) == []
    far = [(4805, 4813, 4804, 4812.25), (4812, 4813, 4811, 4812), (4812, 4813, 4811, 4812)]
    trades = run_backtest(_one_day(far), _cfg(confirm_beyond_ticks=8))
    assert len(trades) == 1 and trades[0].entry_price == 4812.0    # next open


# --------------------------------------------------------------------------
# H2 — band regime filter
# --------------------------------------------------------------------------

def test_band_filter_trades_only_the_middle():
    def breakout(or_high, n=30):
        return [(or_high + 1 + i * 0.5,) * 4 for i in range(n)]
    rows, day0, d = [], dt.date(2024, 1, 1), 0
    for i in range(20):  # history: OR sizes alternate 8 and 12
        lo, hi = (4802, 4810) if i % 2 == 0 else (4798, 4810)
        rows += _day_rows(day0 + dt.timedelta(days=d), lo, hi,
                          [(4806, 4807, 4805, 4806)] * 30); d += 1
    day_mid = day0 + dt.timedelta(days=d)     # OR 10 -> pctile 50: in [30, 70]
    rows += _day_rows(day_mid, 4800, 4810, breakout(4810)); d += 1
    day_big = day0 + dt.timedelta(days=d)     # OR 14 -> pctile 100: blocked
    rows += _day_rows(day_big, 4796, 4810, breakout(4810)); d += 1
    day_small = day0 + dt.timedelta(days=d)   # OR 6 -> pctile 0: blocked
    rows += _day_rows(day_small, 4804, 4810, breakout(4810)); d += 1
    df = _frame(rows)

    cfg = _cfg(or_pctile_min=30.0, or_pctile_max=70.0)
    assert [t.day for t in run_backtest(df, cfg)] == [day_mid]
    control = run_backtest(df, _cfg())
    assert [t.day for t in control] == [day_mid, day_big, day_small]


# --------------------------------------------------------------------------
# H3 — time exits + target "none"
# --------------------------------------------------------------------------

def test_max_hold_exits_at_open_and_no_target_fires():
    after = [
        (4805, 4811, 4804, 4806),   # entry at trigger 4810.25 (10:00)
        (4806, 4807, 4805, 4806),
        (4806, 4807, 4805, 4806),
        (4806, 4890, 4805, 4807),   # huge spike: any R-target would fill here
        (4807, 4808, 4806, 4807),
        (4807, 4808, 4806, 4807),   # 10:05 -> time exit at THIS open
        (4807, 4808, 4806, 4807),
    ]
    cfg = _cfg(target_type="none", max_hold_minutes=5)
    trades = run_backtest(_one_day(after), cfg)
    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "time"
    assert t.exit_time.time() == dt.time(10, 5)
    assert t.exit_price == 4807.0                        # that bar's open


def test_exit_at_clock_time():
    after = [(4805, 4811, 4804, 4806)] + [(4806, 4807, 4805, 4806)] * 130
    cfg = _cfg(target_type="none", exit_at_time="12:00")
    trades = run_backtest(_one_day(after), cfg)
    assert len(trades) == 1
    assert trades[0].exit_reason == "time"
    assert trades[0].exit_time.time() == dt.time(12, 0)


# --------------------------------------------------------------------------
# (g) benchmark-alpha gate criterion
# --------------------------------------------------------------------------

def test_benchmark_alpha_criterion():
    from test_gate import _baseline
    trades = _baseline()          # net +$6,000, passes a-f
    beat = profit_gate("x", trades, 126, benchmark_usd=5000.0)
    assert beat.criteria["g"] is True and beat.passes
    lose = profit_gate("x", trades, 126, benchmark_usd=7000.0)
    assert lose.criteria["g"] is False and not lose.passes
    legacy = profit_gate("x", trades, 126)                 # round-2 mode: no (g)
    assert "g" not in legacy.criteria and legacy.passes


# --------------------------------------------------------------------------
# round-3-corrected harness: the range gate is percentage-of-price
# --------------------------------------------------------------------------

def _priced_day(or_low, or_high):
    """One day at arbitrary price level with a clean post-OR breakout."""
    after = []
    for i in range(30):
        px = or_high + (or_high - or_low) * 0.02 * (i + 1)
        after.append((px, px + 1, px - 1, px))
    return _frame(_day_rows(dt.date(2024, 1, 2), or_low, or_high, after))


def test_range_gate_is_price_relative():
    cfg = _cfg()  # defaults: min 0.02%, max 2.0% of the OR midpoint
    # ES-level intent preserved: ~5000 px, 100-pt OR (2.0%) allowed at the
    # boundary; 110-pt OR (2.2%) blocked — matches the old 400-tick cap.
    assert len(run_backtest(_priced_day(4950, 5049), cfg)) == 1     # 99pt/~5000 = 1.98%
    assert run_backtest(_priced_day(4945, 5055), cfg) == []         # 110pt = 2.2%
    # NQ-level regression: 100-pt OR at ~25,000 is 0.4% — the old tick cap
    # wrongly blocked this; the percentage gate must allow it.
    assert len(run_backtest(_priced_day(24950, 25050), cfg)) == 1   # 0.40%
    assert run_backtest(_priced_day(24700, 25300), cfg) == []       # 600pt = 2.4%
    # tiny-range floor scales too: 0.5-pt OR at 5000 (0.01%) blocked
    assert run_backtest(_priced_day(4999.75, 5000.25), cfg) == []


# --------------------------------------------------------------------------
# hard boundary: executor refuses round-3 configs
# --------------------------------------------------------------------------

def test_engine_refuses_round3_features():
    from orb.live import OrbSessionEngine
    for kw in (dict(confirm_closes=2), dict(confirm_beyond_ticks=8),
               dict(or_pctile_min=30.0, or_pctile_max=70.0),
               dict(max_hold_minutes=120), dict(exit_at_time="12:00"),
               dict(target_type="none")):
        try:
            OrbSessionEngine(_cfg(**kw))
            raise AssertionError(f"engine accepted round-3 config {kw}")
        except NotImplementedError:
            pass


TESTS = [
    test_nq_cost_math,
    test_touch_without_close_does_not_enter,
    test_confirm_closes_enters_next_open,
    test_close_back_inside_resets_count,
    test_confirm_beyond_ticks,
    test_band_filter_trades_only_the_middle,
    test_max_hold_exits_at_open_and_no_target_fires,
    test_exit_at_clock_time,
    test_benchmark_alpha_criterion,
    test_range_gate_is_price_relative,
    test_engine_refuses_round3_features,
]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"all {len(TESTS)} round-3 tests passed")
