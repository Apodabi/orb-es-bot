"""In-sample / out-of-sample validation of the predefined VARIANTS.

Selection is IN-SAMPLE ONLY: the variants are ranked on the in-sample period,
and a single winner is chosen there. Only that pre-committed pick is then
evaluated out-of-sample — giving every variant a shot at the holdout and
keeping whichever looks best is just curve-fitting with extra steps (10
variants x one holdout = 10 lottery tickets).

The pick is "live-eligible" only if it clears the 60% win-rate gate AND stays
net-positive on BOTH periods with enough trades to mean anything.
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
    ap.add_argument("--source-tz", default="UTC", help="tz of raw CSV timestamps (all repo fetchers emit UTC)")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=20)
    args = ap.parse_args()

    df = load_bars(args.data, source_tz=args.source_tz)
    is_df, oos_df = chronological_split(df, args.train_frac)
    print(f"\nData: {args.data}")
    print(f"  in-sample:     {is_df.index[0].date()} -> {is_df.index[-1].date()}")
    print(f"  out-of-sample: {oos_df.index[0].date()} -> {oos_df.index[-1].date()}")
    print(f"  gate: {WIN_RATE_GATE*100:.0f}% win rate + net-positive on BOTH periods\n")

    # 1) Rank every variant on the in-sample period only.
    is_rows = [(cfg, summarize(cfg.name, run_backtest(is_df, cfg))) for cfg in VARIANTS]
    is_rows.sort(key=lambda r: r[1].expectancy_usd, reverse=True)

    print("In-sample ranking (selection happens HERE, before touching the holdout):")
    print(f"{'variant':<28} {'IS win':>7} {'IS net':>10} {'IS n':>5}  IS-eligible")
    eligible = []
    for cfg, m in is_rows:
        ok = m.passes_gate and m.net_usd > 0 and m.n_trades >= args.min_trades
        if ok:
            eligible.append((cfg, m))
        print(f"{m.name:<28} {m.win_rate*100:>6.1f}% {m.net_usd:>10,.0f} {m.n_trades:>5}  {'YES' if ok else '-'}")

    print()
    if not eligible:
        print("No variant is IS-eligible; the out-of-sample period was NOT evaluated")
        print("(preserving the holdout for a future run). Do NOT go live.")
        return

    # 2) Evaluate ONLY the pre-committed top pick on the holdout.
    pick_cfg, m_is = eligible[0]
    m_oos = summarize(pick_cfg.name, run_backtest(oos_df, pick_cfg))
    robust = (
        m_oos.passes_gate and m_oos.net_usd > 0 and m_oos.n_trades >= args.min_trades
    )
    print(f"Selected on IS: {pick_cfg.name}  (the ONLY variant tested out-of-sample)")
    print(f"  OOS: win={m_oos.win_rate*100:.1f}%  net=${m_oos.net_usd:,.0f}  n={m_oos.n_trades}")
    print()
    if robust:
        print(f"Live-eligible (robust IS+OOS): {pick_cfg.name}")
    else:
        print(f"{pick_cfg.name} failed out-of-sample. Do NOT go live.")
        print("NOTE: the holdout has now been consumed by this variant. Re-running "
              "validation with tweaked variants against the same data overfits the "
              "holdout — fetch fresh data before validating again.")


if __name__ == "__main__":
    main()
