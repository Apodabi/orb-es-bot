"""Fetch real ES futures 1-minute bars from Yahoo Finance — no account needed.

Yahoo serves 1-minute bars for the front-month ES contract (ticker ES=F) for
roughly the last 30 calendar days, at most ~7 days per request, so this walks
back in chunks and stitches the result into the repo's canonical CSV:

    columns: timestamp, open, high, low, close, volume   (timestamp in UTC)

    python scripts/fetch_yf.py --out data/es_yf_1min.csv
    python scripts/compare.py  --data data/es_yf_1min.csv --source-tz UTC

This is the fastest way to run the pipeline on REAL bars. Caveats vs the IBKR
routes: only ~30 days of history (fine for smoke-testing an edge, too short to
validate one), and ES=F is Yahoo's own front-month splice — if the window spans
a quarterly roll (~8 days before the third Friday of Mar/Jun/Sep/Dec), the
cutover session may mix contracts. For serious validation use
scripts/fetch_data.py --start/--end (IBKR, roll-correct stitching).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

CHUNK_DAYS = 7
MAX_DAYS = 29  # Yahoo rejects 1m requests older than ~30 days


def fetch_es_yf(days: int = MAX_DAYS, ticker: str = "ES=F") -> pd.DataFrame:
    import yfinance as yf

    days = min(days, MAX_DAYS)
    now = dt.datetime.now(dt.timezone.utc)
    frames = []
    end = now
    remaining = days
    while remaining > 0:
        chunk = min(CHUNK_DAYS, remaining)
        start = end - dt.timedelta(days=chunk)
        df = yf.download(ticker, interval="1m", start=start, end=end,
                         progress=False, auto_adjust=False, prepost=True)
        if df is not None and len(df):
            frames.append(df)
        end = start
        remaining -= chunk
    if not frames:
        raise RuntimeError(f"Yahoo returned no {ticker} 1m bars — try again during market hours "
                           "or check connectivity.")

    out = pd.concat(frames)
    if isinstance(out.columns, pd.MultiIndex):  # yfinance >=0.2 returns (field, ticker)
        out.columns = [c[0] for c in out.columns]
    out = out.rename(columns={c: c.lower() for c in out.columns})
    out = out[["open", "high", "low", "close", "volume"]]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=["open", "high", "low", "close"])

    # canonical CSV shape: UTC, tz-naive timestamp column (pair with --source-tz UTC)
    out.index = out.index.tz_convert("UTC").tz_localize(None)
    out = out.reset_index()
    out = out.rename(columns={out.columns[0]: "timestamp"})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch real ES 1-minute bars from Yahoo Finance.")
    ap.add_argument("--days", type=int, default=MAX_DAYS, help=f"calendar days of history (max ~{MAX_DAYS})")
    ap.add_argument("--ticker", default="ES=F", help="ES=F (E-mini) or MES=F (micro)")
    ap.add_argument("--out", default="data/es_yf_1min.csv")
    args = ap.parse_args()

    df = fetch_es_yf(days=args.days, ticker=args.ticker)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    sessions = pd.to_datetime(df["timestamp"]).dt.normalize().nunique()
    print(f"saved {len(df):,} bars across {sessions} calendar days -> {args.out}")
    print("Timestamps are UTC; backtest with --source-tz UTC.")


if __name__ == "__main__":
    main()
