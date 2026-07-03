"""Performance metrics + the legacy 60% win-rate gate + the round-2 profit gate.

The win-rate gate (round 1) is kept ONLY so historical comparisons still run.
Round 2 onward selects with `profit_gate` (research/ROUND2_PREREG.md §1):
win rate is a reported statistic and must never filter anything.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .backtest import Trade

WIN_RATE_GATE = 0.60  # LEGACY (round 1) — see profit_gate for the current gate


@dataclass
class Metrics:
    name: str
    n_trades: int
    wins: int
    losses: int
    scratches: int
    win_rate: float
    net_usd: float
    profit_factor: float
    avg_win_usd: float
    avg_loss_usd: float
    expectancy_usd: float       # average PnL per trade
    avg_r: float                # average realized R multiple
    max_drawdown_usd: float
    passes_gate: bool

    def line(self) -> str:
        gate = "PASS" if self.passes_gate else "fail"
        return (
            f"{self.name:<28} n={self.n_trades:<4} "
            f"win={self.win_rate*100:5.1f}%  net=${self.net_usd:>10,.0f}  "
            f"PF={self.profit_factor:>4.2f}  exp=${self.expectancy_usd:>7.1f}  "
            f"avgR={self.avg_r:>5.2f}  maxDD=${self.max_drawdown_usd:>9,.0f}  [{gate}]"
        )


def _max_drawdown(equity: list[float]) -> float:
    # Equity starts at 0 before the first trade, so the peak is seeded at 0 —
    # seeding with equity[0] would hide a drawdown that starts on trade one.
    peak = 0.0
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return abs(mdd)


def summarize(name: str, trades: list[Trade], gate: float = WIN_RATE_GATE) -> Metrics:
    n = len(trades)
    if n == 0:
        return Metrics(name, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False)

    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]
    scratches = n - len(wins) - len(losses)

    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = -sum(t.pnl_usd for t in losses)
    net = sum(t.pnl_usd for t in trades)

    equity, running = [], 0.0
    for t in trades:
        running += t.pnl_usd
        equity.append(running)

    decided = len(wins) + len(losses)
    win_rate = len(wins) / decided if decided else 0.0

    return Metrics(
        name=name,
        n_trades=n,
        wins=len(wins),
        losses=len(losses),
        scratches=scratches,
        win_rate=win_rate,
        net_usd=net,
        profit_factor=(gross_profit / gross_loss) if gross_loss else float("inf"),
        avg_win_usd=(gross_profit / len(wins)) if wins else 0.0,
        avg_loss_usd=(-gross_loss / len(losses)) if losses else 0.0,
        expectancy_usd=net / n,
        avg_r=sum(t.r_multiple for t in trades) / n,
        max_drawdown_usd=_max_drawdown(equity),
        passes_gate=win_rate >= gate,
    )


# ---------------------------------------------------------------------------
# Round-2 profit gate (research/ROUND2_PREREG.md §1). All six criteria must
# hold on OUT-OF-SAMPLE trades. The trades MUST have been generated with
# doubled slippage (slippage_ticks=2.0) — this function cannot verify that;
# the round-2 runner enforces it.
# ---------------------------------------------------------------------------

@dataclass
class GateReport:
    name: str
    n_trades: int
    n_decided: int
    win_rate: float            # REPORTED ONLY — never filters (prereg §1)
    net_usd: float
    profit_factor: float
    ex_top_days_usd: float     # net after removing the `strip_days` best days
    max_month_frac: float      # largest positive month / gross of positive months
    max_drawdown_usd: float
    annualized_usd: float
    criteria: dict = field(default_factory=dict)   # {'a'..'f': bool}
    passes: bool = False

    def line(self) -> str:
        c = self.criteria
        flags = "".join(k if v else "-" for k, v in sorted(c.items()))
        return (
            f"{self.name:<24} n={self.n_decided:<4} net=${self.net_usd:>9,.0f}  "
            f"PF={self.profit_factor:>4.2f}  ex-top5=${self.ex_top_days_usd:>8,.0f}  "
            f"maxMo={self.max_month_frac*100:4.0f}%  DD=${self.max_drawdown_usd:>8,.0f}  "
            f"win={self.win_rate*100:4.1f}%*  [{flags}] {'PASS' if self.passes else 'fail'}"
        )


def profit_gate(
    name: str,
    trades: list[Trade],
    oos_sessions: int,
    *,
    min_pf: float = 1.25,
    min_decided: int = 40,
    strip_days: int = 5,
    month_cap: float = 0.40,
    dd_mult: float = 1.5,
) -> GateReport:
    """Evaluate the six-criterion round-2 profit gate on a list of trades.

    (a) net P&L > 0                      (d) net still > 0 after removing the
    (b) profit factor >= min_pf              `strip_days` highest-P&L days
    (c) >= min_decided decided trades    (e) no month > month_cap of gross profit
                                         (f) max drawdown < dd_mult x annualized

    `oos_sessions` is the number of trading sessions in the evaluation window
    (used to annualize: net * 252 / oos_sessions).
    """
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]
    decided = len(wins) + len(losses)
    win_rate = len(wins) / decided if decided else 0.0

    net = sum(t.pnl_usd for t in trades)
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = -sum(t.pnl_usd for t in losses)
    pf = (gross_profit / gross_loss) if gross_loss else (float("inf") if gross_profit else 0.0)

    day_pnl: dict = defaultdict(float)
    month_pnl: dict = defaultdict(float)
    for t in trades:
        day_pnl[t.day] += t.pnl_usd
        month_pnl[t.day.strftime("%Y-%m")] += t.pnl_usd
    top_days = sorted(day_pnl.values(), reverse=True)[:strip_days]
    ex_top = net - sum(top_days)

    pos_months = [v for v in month_pnl.values() if v > 0]
    gross_pos = sum(pos_months)
    max_month_frac = (max(pos_months) / gross_pos) if gross_pos > 0 else 1.0

    equity = peak = 0.0
    mdd = 0.0
    for t in trades:
        equity += t.pnl_usd
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)

    annualized = net * 252.0 / oos_sessions if oos_sessions > 0 else 0.0

    criteria = {
        "a": net > 0,
        "b": pf >= min_pf,
        "c": decided >= min_decided,
        "d": ex_top > 0,
        "e": max_month_frac <= month_cap,
        "f": net > 0 and mdd < dd_mult * annualized,
    }
    return GateReport(
        name=name,
        n_trades=len(trades),
        n_decided=decided,
        win_rate=win_rate,
        net_usd=net,
        profit_factor=pf,
        ex_top_days_usd=ex_top,
        max_month_frac=max_month_frac,
        max_drawdown_usd=mdd,
        annualized_usd=annualized,
        criteria=criteria,
        passes=all(criteria.values()),
    )
