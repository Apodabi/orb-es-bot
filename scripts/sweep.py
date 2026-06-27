"""Parameter sweep with out-of-sample validation.

Runs the full config grid (orb/sweep.py) on the in-sample period, ranks the
survivors, then re-tests the top configs out-of-sample. The point is to surface
configs whose edge *persists* OOS — not the ones that merely curve-fit IS.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.sweep import build_grid
from orb.data import load_bars, chronological_split
from orb.backtest import run_backtest
from orb.metrics import summarize, WIN_RATE_GATE


def main() -> None:
    ap = argparse.ArgumentParser(description="Grid sweep with OOS validation.")
    ap.add_argument("--data", default="data/sample_es.csv")
    ap.add_argument("--source-tz", default="America/New_York")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--top", type=int, default=15, help="how many IS leaders to re-test OOS")
    ap.add_argument("--out", default="data/sweep_results.csv")
    args = ap.parse_args()

    df = load_bars(args.data, source_tz=args.source_tz)
    is_df, oos_df = chronological_split(df, args.train_frac)
    grid = build_grid()

    print(f"\nData: {args.data}")
    print(f"  in-sample:     {is_df.index[0].date()} -> {is_df.index[-1].date()}")
    print(f"  out-of-sample: {oos_df.index[0].date()} -> {oos_df.index[-1].date()}")
    print(f"  grid size: {len(grid)} configs   gate: {WIN_RATE_GATE*100:.0f}% win\n")

    # 1) Evaluate the whole grid in-sample.
    is_results = []
    for cfg in grid:
        m = summarize(cfg.name, run_backtest(is_df, cfg))
        is_results.append((cfg, m))

    # 2) Keep configs that clear the gate, are net-positive, and trade enough.
    survivors = [
        (cfg, m) for cfg, m in is_results
        if m.passes_gate and m.net_usd > 0 and m.n_trades >= args.min_trades
    ]
    survivors.sort(key=lambda r: r[1].expectancy_usd, reverse=True)

    # 3) Re-test the IS leaders out-of-sample.
    leaders = survivors[: args.top] if survivors else sorted(
        is_results, key=lambda r: r[1].expectancy_usd, reverse=True
    )[: args.top]

    print(f"{len(survivors)}/{len(grid)} configs cleared the IS gate. Top {len(leaders)} re-tested OOS:\n")
    print(f"{'config':<26} {'IS win':>7} {'IS net':>9} {'OOS win':>8} {'OOS net':>9}  robust")

    robust = []
    for cfg, m_is in leaders:
        m_oos = summarize(cfg.name, run_backtest(oos_df, cfg))
        is_robust = (
            m_is.passes_gate and m_oos.passes_gate
            and m_is.net_usd > 0 and m_oos.net_usd > 0
            and m_oos.n_trades >= args.min_trades
        )
        if is_robust:
            robust.append(cfg.name)
        print(
            f"{cfg.name:<26} {m_is.win_rate*100:>6.1f}% {m_is.net_usd:>9,.0f} "
            f"{m_oos.win_rate*100:>7.1f}% {m_oos.net_usd:>9,.0f}  {'YES' if is_robust else '-'}"
        )

    # 4) Persist the full in-sample grid for offline analysis.
    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["config", "n_trades", "win_rate", "net_usd", "profit_factor", "expectancy_usd", "avg_r", "max_dd_usd", "passes_gate"])
        for cfg, m in sorted(is_results, key=lambda r: r[1].expectancy_usd, reverse=True):
            w.writerow([cfg.name, m.n_trades, f"{m.win_rate:.4f}", f"{m.net_usd:.2f}",
                        f"{m.profit_factor:.4f}", f"{m.expectancy_usd:.2f}", f"{m.avg_r:.4f}",
                        f"{m.max_drawdown_usd:.2f}", m.passes_gate])

    print(f"\nFull IS grid -> {args.out}")
    if robust:
        print("Robust across IS+OOS:", ", ".join(robust))
    else:
        print("No config survived OOS. Expected on random data; keep iterating on real ES bars.")


if __name__ == "__main__":
    main()
