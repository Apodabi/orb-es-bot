"""Performance metrics + the 60% win-rate gate."""

from __future__ import annotations

from dataclasses import dataclass

from .backtest import Trade

WIN_RATE_GATE = 0.60  # minimum backtest win rate before a variant is live-eligible


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
