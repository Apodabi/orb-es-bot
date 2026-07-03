"""Unit tests for the round-2 profit gate (research/ROUND2_PREREG.md §1).

Each criterion gets a hand-built trade list that isolates it: the baseline
passes everything; each subsequent case breaks exactly the criterion under
test (asserted on the per-criterion flag, not just the overall verdict).
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.backtest import Trade
from orb.metrics import profit_gate


def T(day: str, pnl: float) -> Trade:
    d = dt.date.fromisoformat(day)
    t0 = dt.datetime.combine(d, dt.time(10, 0))
    return Trade(day=d, direction="long", entry_time=t0, entry_price=0.0,
                 exit_time=t0, exit_price=0.0,
                 exit_reason="target" if pnl > 0 else "stop",
                 risk_points=1.0, pnl_points=0.0, pnl_usd=pnl, r_multiple=0.0)


def _baseline() -> list[Trade]:
    """60 decided trades over 6 months, one per weekday-ish date.

    Per month: 6 wins of +$300, 4 losses of -$200 -> month net +$1,000.
    Totals: net +$6,000, PF = 10800/4800 = 2.25, top-5 days = $1,500,
    max month = 1000/6000 = 16.7%, drawdown = $800 (the 4-loss streak),
    annualized (126 sessions) = $12,000 -> DD limit $18,000.
    """
    trades = []
    for m in range(1, 7):
        for i in range(6):
            trades.append(T(f"2024-{m:02d}-{2 + i:02d}", 300.0))
        for i in range(4):
            trades.append(T(f"2024-{m:02d}-{10 + i:02d}", -200.0))
    return trades


def test_baseline_passes_all():
    r = profit_gate("base", _baseline(), oos_sessions=126)
    assert r.criteria == {k: True for k in "abcdef"}, r.criteria
    assert r.passes
    assert abs(r.net_usd - 6000.0) < 1e-9
    assert abs(r.profit_factor - 2.25) < 1e-9
    assert r.n_decided == 60
    assert abs(r.win_rate - 0.6) < 1e-9  # reported, but never a criterion


def test_a_net_must_be_positive():
    trades = [T(f"2024-01-{d:02d}", -100.0) for d in range(1, 29)] * 2  # 56 losses
    r = profit_gate("neg", trades, oos_sessions=126)
    assert r.criteria["a"] is False
    assert r.criteria["c"] is True  # plenty of decided trades — (a) is the failure
    assert not r.passes


def test_b_profit_factor_floor():
    # 25 days each holding one +$220 win and one -$200 loss -> +$20/day.
    # Net +$500 > 0, decided 50, PF = 5500/5000 = 1.10 < 1.25.
    # Top-5 days removal: 5*$20 -> ex-top $400 > 0. Months ~ +$100 each.
    trades = []
    for m in range(1, 6):
        for d in range(1, 6):
            day = f"2024-{m:02d}-{d:02d}"
            trades += [T(day, 220.0), T(day, -200.0)]
    r = profit_gate("thin", trades, oos_sessions=126)
    assert r.criteria["b"] is False
    for k in "acde":
        assert r.criteria[k] is True, (k, r.criteria)
    assert not r.passes


def test_c_minimum_decided_trades():
    # Same healthy shape as baseline but only 30 decided trades.
    trades = []
    for m in range(1, 6):
        trades += [T(f"2024-{m:02d}-{2 + i:02d}", 300.0) for i in range(4)]
        trades += [T(f"2024-{m:02d}-{10 + i:02d}", -200.0) for i in range(2)]
    r = profit_gate("few", trades, oos_sessions=126)
    assert r.n_decided == 30
    assert r.criteria["c"] is False
    assert r.criteria["a"] is True
    assert not r.passes


def test_c_scratches_do_not_count_as_decided():
    trades = _baseline()[:39] + [T("2024-06-20", 0.0)] * 10  # 39 decided + scratches
    r = profit_gate("scratchy", trades, oos_sessions=126)
    assert r.n_decided == 39
    assert r.criteria["c"] is False


def test_d_top_day_concentration():
    # 5 huge single-day wins carry everything; strip them and it's deep red.
    trades = []
    for m in range(1, 6):
        trades.append(T(f"2024-{m:02d}-02", 2000.0))            # one monster day/month
        trades += [T(f"2024-{m:02d}-{5 + i:02d}", 20.0) for i in range(4)]
        trades += [T(f"2024-{m:02d}-{15 + i:02d}", -350.0) for i in range(4)]
    r = profit_gate("lumpy", trades, oos_sessions=126)
    # net = 5*(2000 + 80 - 1400) = +3400; ex-top5 = 3400 - 10000 = -6600
    assert r.criteria["a"] is True
    assert r.criteria["d"] is False
    assert not r.passes


def test_e_month_concentration():
    # All profit lives in June; other months bleed slightly.
    trades = [T(f"2024-06-{d:02d}", 450.0) for d in range(1, 29) if d <= 24]  # +10,800
    for m in (1, 2, 3, 4, 5):
        trades += [T(f"2024-{m:02d}-{d:02d}", -80.0) for d in range(1, 6)]    # -400/mo
    r = profit_gate("one-month", trades, oos_sessions=126)
    assert r.max_month_frac == 1.0
    assert r.criteria["e"] is False
    assert r.criteria["a"] is True
    assert not r.passes


def test_f_drawdown_vs_annualized():
    # Net +$500 over a full year (252 sessions -> annualized $500, DD cap $750)
    # with a -$2,000 losing streak in the middle.
    trades = [T(f"2024-01-{d:02d}", 100.0) for d in range(1, 26)]      # +2,500
    trades += [T(f"2024-06-{d:02d}", -100.0) for d in range(1, 21)]    # -2,000 streak
    r = profit_gate("deep-dd", trades, oos_sessions=252)
    assert abs(r.net_usd - 500.0) < 1e-9
    assert r.max_drawdown_usd == 2000.0
    assert r.criteria["f"] is False
    assert r.criteria["c"] is True
    assert not r.passes


def test_win_rate_never_filters():
    # A 35%-win-rate strategy with strong asymmetry must PASS: the whole point
    # of round 2 is that win rate is reported, not gating.
    trades = []
    for m in range(1, 7):
        trades += [T(f"2024-{m:02d}-{2 + i:02d}", 900.0) for i in range(4)]    # 4 wins
        trades += [T(f"2024-{m:02d}-{10 + i:02d}", -250.0) for i in range(7)]  # 7 losses
    r = profit_gate("low-win", trades, oos_sessions=126)
    # per month: +3600 - 1750 = +1850; PF = 21600/10500 = 2.06; win rate 36.4%
    assert r.win_rate < 0.40
    assert r.passes, r.criteria


TESTS = [
    test_baseline_passes_all,
    test_a_net_must_be_positive,
    test_b_profit_factor_floor,
    test_c_minimum_decided_trades,
    test_c_scratches_do_not_count_as_decided,
    test_d_top_day_concentration,
    test_e_month_concentration,
    test_f_drawdown_vs_annualized,
    test_win_rate_never_filters,
]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"all {len(TESTS)} gate tests passed")
