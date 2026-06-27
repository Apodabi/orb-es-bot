"""Run a single ORB variant over a dataset and print its metrics + trade log."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from orb.config import VARIANTS, StrategyConfig
from orb.data import load_bars
from orb.backtest import run_backtest
from orb.metrics import summarize


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest one ORB variant.")
    ap.add_argument("--data", default="data/sample_es.csv")
    ap.add_argument("--source-tz", default="America/New_York", help="tz of raw timestamps (UTC for IBKR exports)")
    ap.add_argument("--variant", default=None, help="variant name from config.VARIANTS (default: first)")
    ap.add_argument("--trades", action="store_true", help="print the full trade log")
    args = ap.parse_args()

    cfg = next((v for v in VARIANTS if v.name == args.variant), VARIANTS[0]) if args.variant else VARIANTS[0]

    df = load_bars(args.data, source_tz=args.source_tz)
    trades = run_backtest(df, cfg)
    m = summarize(cfg.name, trades)

    print(f"\nData: {args.data}  ({df.index[0].date()} -> {df.index[-1].date()}, {len(df):,} bars)")
    print(f"Variant: {cfg.name}\n")
    print(m.line())

    if args.trades:
        print("\nday         dir    entry     exit   reason     R     pnl$")
        for t in trades:
            print(
                f"{t.day}  {t.direction:<5} {t.entry_price:>8.2f} {t.exit_price:>8.2f}"
                f"  {t.exit_reason:<7} {t.r_multiple:>5.2f} {t.pnl_usd:>8.1f}"
            )


if __name__ == "__main__":
    main()
