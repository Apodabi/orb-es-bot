"""Parameter sweep with out-of-sample validation.

Runs the full config grid (orb/sweep.py) on the in-sample period, ranks the
survivors, then re-tests ONLY the single top survivor out-of-sample. Testing
the whole leaderboard on the holdout and keeping whatever sticks is multiple
testing — with 15 shots at one OOS window, something will pass by luck and
get promoted as "robust". One pre-committed pick, one holdout evaluation.

The full in-sample grid is persisted to CSV for offline analysis.
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
    ap.add_argument("--source-tz", default="UTC", help="tz of raw CSV timestamps (all repo fetchers emit UTC)")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--top", type=int, default=15, help="how many IS leaders to display")
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
    is_results = [(cfg, summarize(cfg.name, run_backtest(is_df, cfg))) for cfg in grid]

    # 2) Keep configs that clear the gate, are net-positive, and trade enough.
    survivors = [
        (cfg, m) for cfg, m in is_results
        if m.passes_gate and m.net_usd > 0 and m.n_trades >= args.min_trades
    ]
    survivors.sort(key=lambda r: r[1].expectancy_usd, reverse=True)

    # 3) Show the IS leaderboard (informational — nothing here touched the holdout).
    board = survivors if survivors else sorted(
        is_results, key=lambda r: r[1].expectancy_usd, reverse=True
    )
    print(f"{len(survivors)}/{len(grid)} configs cleared the IS gate. "
          f"Top {min(args.top, len(board))} in-sample{'' if survivors else ' (nothing cleared the gate)'}:\n")
    print(f"{'config':<26} {'IS win':>7} {'IS net':>9} {'IS n':>5}")
    for cfg, m in board[: args.top]:
        print(f"{cfg.name:<26} {m.win_rate*100:>6.1f}% {m.net_usd:>9,.0f} {m.n_trades:>5}")

    # 4) Persist the full in-sample grid for offline analysis.
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["config", "n_trades", "win_rate", "net_usd", "profit_factor", "expectancy_usd", "avg_r", "max_dd_usd", "passes_gate"])
        for cfg, m in sorted(is_results, key=lambda r: r[1].expectancy_usd, reverse=True):
            w.writerow([cfg.name, m.n_trades, f"{m.win_rate:.4f}", f"{m.net_usd:.2f}",
                        f"{m.profit_factor:.4f}", f"{m.expectancy_usd:.2f}", f"{m.avg_r:.4f}",
                        f"{m.max_drawdown_usd:.2f}", m.passes_gate])
    print(f"\nFull IS grid -> {args.out}")

    # 5) One pre-committed pick, one holdout evaluation.
    print()
    if not survivors:
        print("No config cleared the in-sample bar; the out-of-sample period was NOT")
        print("evaluated (preserving the holdout). Keep iterating on real ES bars.")
        return

    pick_cfg, m_is = survivors[0]
    m_oos = summarize(pick_cfg.name, run_backtest(oos_df, pick_cfg))
    robust = (
        m_oos.passes_gate and m_oos.net_usd > 0
        and m_oos.n_trades >= args.min_trades
    )
    print(f"Selected on IS: {pick_cfg.name}  (the ONLY config tested out-of-sample)")
    print(f"  IS:  win={m_is.win_rate*100:.1f}%  net=${m_is.net_usd:,.0f}  n={m_is.n_trades}")
    print(f"  OOS: win={m_oos.win_rate*100:.1f}%  net=${m_oos.net_usd:,.0f}  n={m_oos.n_trades}")
    if robust:
        print(f"\nRobust across IS+OOS: {pick_cfg.name}")
    else:
        print(f"\n{pick_cfg.name} did not survive OOS. Expected on random data; keep "
              "iterating on real ES bars.")
        print("NOTE: this holdout is now consumed. Widening the grid and re-running "
              "against the same data overfits it — fetch fresh data first.")


if __name__ == "__main__":
    main()
