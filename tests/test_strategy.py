"""Hand-crafted bar sequences that pin down the ORB entry/exit logic."""

import datetime as dt
import io
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from orb.config import StrategyConfig, ES
from orb.backtest import Trade, run_backtest
from orb.data import load_bars, session_slice
from orb.metrics import summarize


ET = ZoneInfo("America/New_York")


def _bars(rows, minute_step=1):
    idx = [dt.datetime.combine(dt.date(2024, 1, 2), dt.time(h, m), ET) for h, m, *_ in rows]
    data = [{"open": o, "high": hi, "low": lo, "close": c} for _, _, o, hi, lo, c in rows]
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx))


def _or_rows(or_high=4810, or_low=4800, n=30, closes=None):
    """30 one-minute OR bars oscillating between or_low and or_high."""
    rows = []
    for i in range(n):
        h, m = 9 + (30 + i) // 60, (30 + i) % 60
        o = or_low if i % 2 else or_high
        c = closes[i] if closes is not None else o
        rows.append((h, m, o, or_high, or_low, c))
    return rows


def _after(rows, path):
    """Append explicit (open, high, low, close) bars starting at 10:00."""
    for i, (o, hi, lo, c) in enumerate(path):
        minute = 30 + i
        rows.append((10 + minute // 60, minute % 60, o, hi, lo, c))
    return rows


def _minute_series(or_high, or_low, breakout_to, n_after=40):
    """Build a day: a 30-min OR, then a clean move to `breakout_to`."""
    rows = _or_rows(or_high, or_low)
    start = or_high if breakout_to > or_high else or_low
    path = []
    for i in range(n_after):
        frac = (i + 1) / n_after
        px = start + (breakout_to - start) * frac
        path.append((px, px + 1, px - 1, px))
    return _bars(_after(rows, path))


def _cfg(**kw):
    base = dict(name="t", slippage_ticks=0, commission_per_side=0, entry_buffer_ticks=1)
    base.update(kw)
    return StrategyConfig(**base)


# --------------------------------------------------------------------------
# original behavior tests
# --------------------------------------------------------------------------

def test_long_breakout_hits_target():
    # OR 4800-4810 (40 ticks). Range stop, 1R target -> long target ~4820.
    df = _minute_series(or_high=4810, or_low=4800, breakout_to=4825)
    cfg = _cfg(direction="long", stop_type="range", target_type="r_multiple", r_multiple=1.0)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "long"
    assert t.exit_reason == "target"
    assert t.pnl_usd > 0


def test_short_breakout_hits_target():
    df = _minute_series(or_high=4810, or_low=4800, breakout_to=4780)
    cfg = _cfg(direction="short", stop_type="range", target_type="r_multiple", r_multiple=1.0)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    assert trades[0].direction == "short"
    assert trades[0].exit_reason == "target"
    assert trades[0].pnl_usd > 0


def test_no_breakout_no_trade():
    # Price never leaves the OR (4800-4810) after the open.
    rows = _or_rows()
    path = [(4805 if i % 2 else 4806, 4808, 4802, 4805 if i % 2 else 4806) for i in range(60)]
    df = _bars(_after(rows, path))
    cfg = _cfg(direction="both", entry_buffer_ticks=2)
    trades = run_backtest(df, cfg)
    assert trades == []


def test_breakeven_stop_protects_entry():
    # Long enters 4811, risk 11. Far 3R target. Price runs to 1R+ (arms
    # breakeven), then reverses through entry -> exits ~flat.
    after = [
        (4811, 4825, 4811, 4824),   # entry bar; high arms nothing yet (entry bar unmanaged)
        (4824, 4824, 4812, 4820),   # >= entry+1R already seen; BE armed here, close above stop
        (4820, 4820, 4804, 4805),   # falls through entry -> breakeven stop
    ]
    df = _bars(_after(_or_rows(), after))
    cfg = _cfg(direction="long", stop_type="range", target_type="r_multiple",
               r_multiple=3.0, breakeven_at_r=1.0)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "stop"
    assert abs(t.r_multiple) < 0.05   # exited at ~breakeven


def test_trailing_stop_locks_in_profit():
    # Long runs up, trailing stop (40 ticks = 10 pts) follows, then a reversal
    # takes us out in profit well above entry.
    after = [
        (4811, 4815, 4810, 4814),   # entry bar (~4811), no exit yet
        (4814, 4835, 4814, 4833),   # runs up: trailing stop follows to ~4825
        (4833, 4833, 4820, 4821),   # dips to 4820 -> hits trailing stop ~4825 in profit
    ]
    df = _bars(_after(_or_rows(), after))
    cfg = _cfg(direction="long", stop_type="range", target_type="r_multiple",
               r_multiple=3.0, trailing_stop_ticks=40)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    assert trades[0].exit_reason == "stop"
    assert trades[0].pnl_usd > 0   # locked in profit, not a loss


def test_max_one_trade_per_day():
    df = _minute_series(or_high=4810, or_low=4800, breakout_to=4830)
    cfg = _cfg(max_trades_per_day=1)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1  # == not <=: the breakout day MUST produce its trade


# --------------------------------------------------------------------------
# regression tests for the audited fill-model bugs
# --------------------------------------------------------------------------

def test_entry_bar_stop_is_honored():
    # Audit repro: entry bar trades 5 points through the stop. A resting stop
    # fills that minute; the sim must NOT hold through it into the next-bar rally.
    after = [
        (4805, 4811, 4795, 4805),   # fills long 4810.25, then trades through 4800 stop
        (4805, 4821, 4805, 4821),   # would be a fake +1R "win" if the stop were skipped
    ]
    df = _bars(_after(_or_rows(), after))
    cfg = _cfg(direction="long", stop_type="range", target_type="r_multiple", r_multiple=1.0)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "stop"
    assert t.exit_time == t.entry_time          # exited on the entry bar
    assert abs(t.r_multiple + 1.0) < 0.01       # a full -1R loss, not a win


def test_entry_bar_ambiguous_conservative_vs_optimistic():
    # Entry bar spans BOTH the stop and the target: conservative books the
    # stop, optimistic the target.
    after = [(4805, 4821, 4795, 4805), (4805, 4806, 4804, 4805)]
    df = _bars(_after(_or_rows(), after))
    con = run_backtest(df, _cfg(direction="long", stop_type="range",
                                target_type="r_multiple", r_multiple=1.0,
                                conservative_fills=True))
    opt = run_backtest(df, _cfg(direction="long", stop_type="range",
                                target_type="r_multiple", r_multiple=1.0,
                                conservative_fills=False))
    assert con[0].exit_reason == "stop" and con[0].pnl_usd < 0
    assert opt[0].exit_reason == "target" and opt[0].pnl_usd > 0


def test_ema_filter_uses_previous_bar_only():
    # Two days identical up to the moment the breakout stop order fills; only
    # the entry bar's CLOSE differs. The close isn't knowable at fill time, so
    # both must produce the same trade.
    closes = [4805 + i * 0.15 for i in range(30)]  # rising: prev close > EMA
    take = []
    for entry_close in (4808.25, 4810.9):
        rows = _or_rows(closes=closes)
        path = [(4809, 4811, 4808, entry_close), (4811, 4812, 4810, 4811),
                (4812, 4822, 4811, 4821)]
        df = _bars(_after(rows, path))
        cfg = _cfg(direction="both", stop_type="fixed", stop_ticks=60,
                   target_type="r_multiple", r_multiple=1.0, ema_trend_filter=5)
        trades = run_backtest(df, cfg)
        take.append([(t.direction, t.entry_time, t.entry_price) for t in trades])
    assert take[0] == take[1] and len(take[0]) == 1


def test_gap_through_stop_fills_at_open():
    # The bar after entry gaps far below the stop: a live stop fills at the
    # open, not at the stop price.
    after = [
        (4811, 4812, 4810.5, 4811.5),  # entry bar, fill 4811, stop 4800
        (4790, 4792, 4788, 4790),      # gaps 10 points through the stop
    ]
    df = _bars(_after(_or_rows(), after))
    cfg = _cfg(direction="long", stop_type="range", target_type="r_multiple", r_multiple=5.0)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    assert trades[0].exit_reason == "stop"
    assert trades[0].exit_price == 4790.0  # the gap open, not the 4800 stop


def test_short_breakeven():
    after = [
        (4799, 4799, 4790, 4791),   # entry bar: fill short 4799 (open below trigger)
        (4791, 4792, 4787, 4791),   # low <= entry-1R: BE arms, stop -> 4799
        (4791, 4801, 4791, 4800),   # rallies through 4799 -> breakeven stop
    ]
    df = _bars(_after(_or_rows(), after))
    cfg = _cfg(direction="short", stop_type="range", target_type="r_multiple",
               r_multiple=3.0, breakeven_at_r=1.0)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    assert trades[0].direction == "short"
    assert trades[0].exit_reason == "stop"
    assert abs(trades[0].r_multiple) < 0.05


def test_no_same_bar_reentry():
    # After an exit, OHLC can't order prices, so re-entry must wait a bar.
    after = [
        (4811, 4811, 4811, 4811),   # entry 1: long 4811, stop 4809, target 4813
        (4811, 4814, 4808, 4813),   # spans both -> conservative stop; high would re-trigger
        (4812, 4812, 4812, 4812),   # re-entry happens HERE, not on the exit bar
    ]
    df = _bars(_after(_or_rows(), after))
    cfg = _cfg(direction="long", stop_type="fixed", stop_ticks=8,
               target_type="fixed", target_ticks=8,
               allow_reentry=True, max_trades_per_day=2)
    trades = run_backtest(df, cfg)
    assert len(trades) == 2
    assert trades[1].entry_time > trades[0].exit_time


def test_late_data_day_is_skipped():
    # Data starts at 09:45 — the true opening range is unknowable.
    rows = []
    for i in range(15, 45):
        h, m = 9 + (30 + i) // 60, (30 + i) % 60
        rows.append((h, m, 4805, 4810, 4800, 4805))
    path = [(4815, 4830, 4815, 4830)] * 10
    df = _bars(_after(rows, path))
    assert run_backtest(df, _cfg()) == []


def test_gap_inside_or_skips_day():
    # One missing minute inside the OR window -> unreliable range -> no trades.
    rows = [r for r in _or_rows() if r[1] != 40]  # drop the 09:40 bar
    path = [(4815, 4830, 4815, 4830)] * 10
    df = _bars(_after(rows, path))
    assert run_backtest(df, _cfg()) == []


def test_five_minute_bars_work():
    # 30-minute OR out of 5-minute bars (6 bars), then a breakout.
    rows = []
    for i in range(6):
        m = 30 + 5 * i
        rows.append((9 + m // 60, m % 60, 4800 if i % 2 else 4810, 4810, 4800, 4805))
    for i in range(20):
        m = 5 * i
        px = 4812 + i * 2
        rows.append((10 + m // 60, m % 60, px, px + 2, px - 2, px + 1))
    df = _bars(rows)
    cfg = _cfg(direction="long", stop_type="range", target_type="r_multiple", r_multiple=1.0)
    trades = run_backtest(df, cfg)
    assert len(trades) == 1
    assert trades[0].exit_reason == "target"


def test_drawdown_counts_first_trade():
    def trade(pnl):
        t0 = dt.datetime(2024, 1, 2, 10, 0, tzinfo=ET)
        return Trade(day=t0.date(), direction="long", entry_time=t0, entry_price=0,
                     exit_time=t0, exit_price=0, exit_reason="stop", risk_points=1,
                     pnl_points=0, pnl_usd=pnl, r_multiple=0)
    m = summarize("t", [trade(-500.0), trade(1000.0)])
    assert m.max_drawdown_usd == 500.0  # was hidden when peak seeded at equity[0]


def test_load_bars_hygiene():
    with tempfile.TemporaryDirectory() as td:
        # NaN OHLC rows are dropped
        p = Path(td, "nan.csv")
        p.write_text("timestamp,open,high,low,close\n"
                     "2024-01-02 10:00:00,1,2,0.5,1.5\n"
                     "2024-01-02 10:01:00,1,,0.5,1.5\n"
                     "2024-01-02 10:02:00,1,2,0.5,1.6\n")
        df = load_bars(str(p), source_tz="America/New_York")
        assert len(df) == 2

        # empty file raises a clear error instead of crashing callers
        p2 = Path(td, "empty.csv")
        p2.write_text("timestamp,open,high,low,close\n")
        try:
            load_bars(str(p2))
            raise AssertionError("expected ValueError on empty CSV")
        except ValueError:
            pass

        # mixed UTC offsets across a DST transition parse fine
        p3 = Path(td, "dst.csv")
        p3.write_text("timestamp,open,high,low,close\n"
                      "2024-03-08 10:00:00-05:00,1,2,0.5,1.5\n"
                      "2024-03-11 10:00:00-04:00,1,2,0.5,1.5\n")
        df3 = load_bars(str(p3))
        assert len(df3) == 2 and str(df3.index.tz) == "America/New_York"

        # duplicate timestamps keep the last row
        p4 = Path(td, "dup.csv")
        p4.write_text("timestamp,open,high,low,close\n"
                      "2024-01-02 10:00:00,1,2,0.5,1.5\n"
                      "2024-01-02 10:00:00,9,9,9,9\n")
        df4 = load_bars(str(p4), source_tz="America/New_York")
        assert len(df4) == 1 and df4["open"].iloc[0] == 9


def test_session_slice_excludes_close_bar():
    idx = pd.DatetimeIndex([
        dt.datetime(2024, 1, 2, 15, 59, tzinfo=ET),
        dt.datetime(2024, 1, 2, 16, 0, tzinfo=ET),   # post-close bar
    ])
    df = pd.DataFrame({"open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [1, 2]}, index=idx)
    out = session_slice(df, "09:30", "16:00")
    assert len(out) == 1 and out.index[0].time() == dt.time(15, 59)


# --------------------------------------------------------------------------
# live-engine parity: the engine that will trade must equal the backtester
# --------------------------------------------------------------------------

def _parity_key(trades):
    return [(t.day, t.direction, t.entry_time, round(t.entry_price, 6),
             t.exit_time, round(t.exit_price, 6), t.exit_reason) for t in trades]


def test_parity_no_management_on_entry_bar():
    # The entry bar spikes past the breakeven trigger. The backtester defers
    # stop management past the fill bar; the live engine must too, or the two
    # book different exits (engine: early BE stop; backtest: later/none).
    from orb.live import ReplayRunner
    after = [
        (4805, 4823, 4805, 4812),   # entry bar: high >= entry+1R — must NOT arm BE yet
        (4808, 4809, 4805, 4808),   # dips below the would-be BE stop; real stop is 4800
        (4808, 4821, 4808, 4820),   # BE arms HERE (first post-entry bar that qualifies)
        (4820, 4820, 4809, 4810),   # breakeven stop hit
    ]
    df = _bars(_after(_or_rows(), after))
    cfg = _cfg(direction="long", stop_type="range", target_type="r_multiple",
               r_multiple=3.0, breakeven_at_r=1.0)
    bt = run_backtest(df, cfg)
    rp = ReplayRunner(cfg).run(df)
    assert _parity_key(bt) == _parity_key(rp)
    assert len(bt) == 1 and bt[0].exit_reason == "stop" and abs(bt[0].r_multiple) < 0.05


def test_parity_no_management_on_flatten_bar():
    # The FINAL session bar spikes past the breakeven trigger then closes
    # lower. The backtester flattens at the close without managing that bar;
    # the engine must do the same (not book a breakeven 'stop' instead).
    from orb.live import ReplayRunner
    path = [(4811, 4812, 4810.5, 4811.5)]                   # 10:30 entry bar, fill 4811
    path += [(4812, 4813, 4811, 4812)] * 328                # flat filler to 15:58
    path += [(4812, 4823, 4806, 4806)]                      # 15:59: spikes >= entry+1R, closes low
    df = _bars(_after(_or_rows(), path))
    cfg = _cfg(direction="long", stop_type="range", target_type="r_multiple",
               r_multiple=3.0, breakeven_at_r=1.0)
    bt = run_backtest(df, cfg)
    rp = ReplayRunner(cfg).run(df)
    assert _parity_key(bt) == _parity_key(rp)
    assert len(bt) == 1 and bt[0].exit_reason == "close" and bt[0].exit_price == 4806.0


def test_live_engine_replay_parity():
    from orb.live import ReplayRunner
    from scripts.make_sample import gen

    raw = gen(days=40, seed=7, start_price=5000.0)
    # gen() emits ET wall-clock strings; index them as ET like load_bars would
    ts = pd.to_datetime(raw["timestamp"]).dt.tz_localize(ET)
    df = raw.drop(columns=["timestamp"]).set_index(pd.DatetimeIndex(ts)).astype(float)

    variants = [
        _cfg(direction="both", stop_type="range", target_type="r_multiple", r_multiple=1.0,
             slippage_ticks=1.0, commission_per_side=2.5),
        _cfg(stop_type="fraction", stop_fraction=0.5, target_type="range_multiple", range_multiple=1.0),
        _cfg(target_type="r_multiple", r_multiple=2.0, breakeven_at_r=1.0, trailing_stop_ticks=40),
        _cfg(target_type="r_multiple", r_multiple=2.0, ema_trend_filter=20),
        _cfg(target_type="r_multiple", r_multiple=1.0, no_entry_after="12:00"),
        _cfg(allow_reentry=True, max_trades_per_day=3, stop_type="fixed", stop_ticks=20,
             target_type="fixed", target_ticks=30),
    ]
    for cfg in variants:
        bt = run_backtest(df, cfg)
        rp = ReplayRunner(cfg).run(df)
        key = lambda ts_: [(t.day, t.direction, t.entry_time, round(t.entry_price, 6),
                            t.exit_time, round(t.exit_price, 6), t.exit_reason) for t in ts_]
        assert key(bt) == key(rp), (
            f"{cfg.name}: engine diverged from backtest\n"
            f"backtest n={len(bt)} vs replay n={len(rp)}\n"
            f"first diff: {next(((a, b) for a, b in zip(key(bt), key(rp)) if a != b), 'length mismatch')}"
        )


TESTS = [
    test_long_breakout_hits_target,
    test_short_breakout_hits_target,
    test_no_breakout_no_trade,
    test_breakeven_stop_protects_entry,
    test_trailing_stop_locks_in_profit,
    test_max_one_trade_per_day,
    test_entry_bar_stop_is_honored,
    test_entry_bar_ambiguous_conservative_vs_optimistic,
    test_ema_filter_uses_previous_bar_only,
    test_gap_through_stop_fills_at_open,
    test_short_breakeven,
    test_no_same_bar_reentry,
    test_late_data_day_is_skipped,
    test_gap_inside_or_skips_day,
    test_five_minute_bars_work,
    test_drawdown_counts_first_trade,
    test_load_bars_hygiene,
    test_session_slice_excludes_close_bar,
    test_parity_no_management_on_entry_bar,
    test_parity_no_management_on_flatten_bar,
    test_live_engine_replay_parity,
]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"all {len(TESTS)} tests passed")
