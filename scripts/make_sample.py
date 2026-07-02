"""Generate a synthetic ES 1-minute dataset so the pipeline runs without IBKR.

This is ONLY for exercising the plumbing. Synthetic random-walk data tells you
nothing about real edge — never judge the 60% gate on it. Replace data/ with
real bars from scripts/fetch_data.py before drawing any conclusion.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import random
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ET_OPEN = dt.time(9, 30)
ET_CLOSE = dt.time(16, 0)
TICK = 0.25


def round_tick(p: float) -> float:
    return round(p / TICK) * TICK


def gen(days: int, seed: int, start_price: float) -> pd.DataFrame:
    rng = random.Random(seed)
    rows = []
    price = start_price
    day = dt.date(2024, 1, 2)
    made = 0
    while made < days:
        if day.weekday() < 5:  # weekdays only
            # Each day gets a random intraday drift so some days trend (breakout-friendly).
            drift = rng.uniform(-0.04, 0.04)
            vol = rng.uniform(0.4, 1.2)
            t = dt.datetime.combine(day, ET_OPEN)
            end = dt.datetime.combine(day, ET_CLOSE)
            while t < end:  # last RTH bar is 15:59; a 16:00 bar would be post-close
                step = rng.gauss(drift, vol)
                o = price
                c = price + step
                hi = max(o, c) + abs(rng.gauss(0, vol)) * 0.5
                lo = min(o, c) - abs(rng.gauss(0, vol)) * 0.5
                rows.append(
                    {
                        "timestamp": t.strftime("%Y-%m-%d %H:%M:%S"),
                        "open": round_tick(o),
                        "high": round_tick(hi),
                        "low": round_tick(lo),
                        "close": round_tick(c),
                        "volume": rng.randint(50, 5000),
                    }
                )
                price = c
                t += dt.timedelta(minutes=1)
            made += 1
            price += rng.gauss(0, 3)  # overnight gap
        day += dt.timedelta(days=1)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic ES 1-min bars (timestamps are ET).")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", type=float, default=4800.0)
    ap.add_argument("--out", default="data/sample_es.csv")
    args = ap.parse_args()

    df = gen(args.days, args.seed, args.start)
    # Emit UTC like every real fetcher does, so ALL data files in this repo
    # share one timestamp convention and --source-tz can default to UTC.
    ts = (pd.to_datetime(df["timestamp"])
          .dt.tz_localize(ZoneInfo("America/New_York"))
          .dt.tz_convert("UTC").dt.tz_localize(None))
    df["timestamp"] = ts.dt.strftime("%Y-%m-%d %H:%M:%S")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"wrote {len(df):,} synthetic bars over {args.days} sessions -> {args.out}")
    print("NOTE: synthetic data — for plumbing only, not edge validation.")


if __name__ == "__main__":
    main()
