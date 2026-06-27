"""In-sample / out-of-sample validation of the predefined VARIANTS.

Picks the best variant on the in-sample period, then reports how it holds up
out-of-sample. A variant is only "robust" if it clears the 60% win-rate gate AND
stays net-positive on BOTH periods — the bar for promoting anything to live.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.config import VARIANTS
from orb.data import load_bars, chronological_split
from orb.backtest import run_backtest
from orb.metrics import summarize, WIN_RATE_GATE


def main() -> None:
    ap = argparse.ArgumentParser(description="IS/OOS validation of predefined variants.")
    ap.add_argument("--data", default="data/sample_es.csv")
    ap.add_argument("--source-tz", default="America/New_York")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=20)
    args = ap.parse_args()

    df = load_bars(args.data, source_tz=args.source_tz)
    is_df, oos_df = chronological_split(df, args.train_frac)
    print(f"\nData: {args.data}")
    print(f"  in-sample:     {is_df.index[0].date()} -> {is_df.index[-1].date()}")
    print(f"  out-of-sample: {oos_df.index[0].date()} -> {oos_df.index[-1].date()}")
    print(f"  gate: {WIN_RATE_GATE*100:.0f}% win rate + net-positive on BOTH periods\n")

    rows = []
    for cfg in VARIANTS:
        m_is = summarize(cfg.name, run_backtest(is_df, cfg))
        m_oos = summarize(cfg.name, run_backtest(oos_df, cfg))
        robust = (
            m_is.passes_gate and m_oos.passes_gate
            and m_is.net_usd > 0 and m_oos.net_usd > 0
            and m_is.n_trades >= args.min_trades and m_oos.n_trades >= args.min_trades
        )
        rows.append((cfg.name, m_is, m_oos, robust))

    # Rank by in-sample expectancy (what you'd actually select on).
    rows.sort(key=lambda r: r[1].expectancy_usd, reverse=True)

    print(f"{'variant':<28} {'IS win':>7} {'IS net':>10} {'OOS win':>8} {'OOS net':>10}  robust")
    for name, m_is, m_oos, robust in rows:
        print(
            f"{name:<28} {m_is.win_rate*100:>6.1f}% {m_is.net_usd:>10,.0f} "
            f"{m_oos.win_rate*100:>7.1f}% {m_oos.net_usd:>10,.0f}  "
            f"{'YES' if robust else '-'}"
        )

    robust_names = [name for name, *_ , r in rows if r]
    print()
    if robust_names:
        print("Live-eligible (robust IS+OOS):", ", ".join(robust_names))
    else:
        print("No variant is robust across IS and OOS. Do NOT go live.")


if __name__ == "__main__":
    main()
