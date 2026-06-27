"""Race every variant in config.VARIANTS and rank them against the 60% gate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.config import VARIANTS
from orb.data import load_bars
from orb.backtest import run_backtest
from orb.metrics import summarize, WIN_RATE_GATE


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare all ORB variants.")
    ap.add_argument("--data", default="data/sample_es.csv")
    ap.add_argument("--source-tz", default="America/New_York")
    args = ap.parse_args()

    df = load_bars(args.data, source_tz=args.source_tz)
    print(f"\nData: {args.data}  ({df.index[0].date()} -> {df.index[-1].date()}, {len(df):,} bars)")
    print(f"Win-rate gate for live eligibility: {WIN_RATE_GATE*100:.0f}%\n")

    results = [summarize(cfg.name, run_backtest(df, cfg)) for cfg in VARIANTS]
    results.sort(key=lambda m: (m.passes_gate, m.win_rate, m.net_usd), reverse=True)

    for m in results:
        print(m.line())

    passing = [m for m in results if m.passes_gate and m.n_trades > 0]
    print()
    if passing:
        print(f"{len(passing)} variant(s) clear the {WIN_RATE_GATE*100:.0f}% gate: "
              + ", ".join(m.name for m in passing))
    else:
        print(f"No variant clears the {WIN_RATE_GATE*100:.0f}% gate on this data.")
    print("\nReminder: validate on REAL ES data and out-of-sample before going live.")


if __name__ == "__main__":
    main()
