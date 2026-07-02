"""Download real ES bars from Interactive Brokers and save to CSV.

Prereqs: TWS or IB Gateway running with the API enabled, ib_insync installed,
and ES market-data permissions on the account.

Roll-correct multi-quarter history (recommended for any backtest > ~2 months):
    python scripts/fetch_data.py --start 2024-01-01 --end 2024-07-01 --out data/es_1min.csv

Single contract (one expiry's active window only):
    python scripts/fetch_data.py --expiry 202409 --duration "30 D" --out data/es_1min.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.ibkr import fetch_es_bars, fetch_es_stitched, save_csv


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch ES historical bars from IBKR.")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD: stitched, roll-correct fetch from this date")
    ap.add_argument("--end", default="", help='stitched mode: YYYY-MM-DD (default today); single mode: IBKR endDateTime, e.g. "20240920 16:00:00 US/Eastern"')
    ap.add_argument("--expiry", default=None, help='single-contract month, e.g. "202409"')
    ap.add_argument("--duration", default="30 D", help="single-contract mode only")
    ap.add_argument("--bar-size", default="1 min")
    ap.add_argument("--roll-days", type=int, default=8, help="stitched mode: cut over this many days before expiry")
    ap.add_argument("--allow-gaps", action="store_true",
                    help="stitched mode: accept (with a warning) contract windows that return no bars")
    ap.add_argument("--port", type=int, default=7497, help="7497 TWS paper, 4002 GW paper, 7496/4001 live")
    ap.add_argument("--out", default="data/es_1min.csv")
    args = ap.parse_args()

    if args.start:
        df = fetch_es_stitched(
            start=args.start,
            end=args.end,
            bar_size=args.bar_size,
            roll_days=args.roll_days,
            port=args.port,
            allow_gaps=args.allow_gaps,
        )
    else:
        try:
            days = int(args.duration.strip().upper().rstrip("D").strip())
        except ValueError:
            days = 0
        if days > 70:
            print("WARNING: a single ES contract is only the front month for ~3 months —")
            print(f"         '{args.duration}' of one expiry will include thin back-month data")
            print("         around the roll. Prefer --start/--end for a stitched fetch.")
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
