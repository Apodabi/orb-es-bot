"""Download real ES bars from Interactive Brokers and save to CSV.

Prereqs: TWS or IB Gateway running with the API enabled, ib_insync installed,
and ES market-data permissions on the account.

Example:
    python scripts/fetch_data.py --expiry 20240920 --duration "60 D" --out data/es_1min.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.ibkr import fetch_es_bars, save_csv


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch ES historical bars from IBKR.")
    ap.add_argument("--expiry", default=None, help='contract month, e.g. "20240920"')
    ap.add_argument("--duration", default="30 D")
    ap.add_argument("--bar-size", default="1 min")
    ap.add_argument("--end", default="", help='endDateTime, e.g. "20240920 16:00:00 US/Eastern" (blank = now)')
    ap.add_argument("--port", type=int, default=7497, help="7497 TWS paper, 4002 GW paper, 7496/4001 live")
    ap.add_argument("--out", default="data/es_1min.csv")
    args = ap.parse_args()

    df = fetch_es_bars(
        end=args.end,
        duration=args.duration,
        bar_size=args.bar_size,
        expiry=args.expiry,
        port=args.port,
    )
    save_csv(df, args.out)
    print(f"saved {len(df):,} bars -> {args.out}")
    print("Timestamps are UTC; backtest with --source-tz UTC.")


if __name__ == "__main__":
    main()
