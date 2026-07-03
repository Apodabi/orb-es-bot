"""Data integrity check for a stitched ES 1-min dataset — run BEFORE backtesting.

Verifies, per month:
  (a) expected months present with a plausible number of trading sessions
  (b) each RTH session starts exactly at 09:30 ET with ~the expected bar count
      (early-close holiday sessions ending ~13:00 ET are classified, not failed)
  (c) no consecutive close-to-close jump > threshold; every jump is annotated
      (intra-session / roll-boundary / plain overnight) — bad roll stitches
      show up as large jumps at contract boundaries
  (d) no duplicate timestamps, no negative or zero volume on RTH bars

Exit code 0 = PASS, 1 = FAIL. On FAIL the offending dates are listed.

    python scripts/check_data.py --data data/es_1min.csv --start-month 2025-07 --end-month 2026-06
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from orb.ibkr import quarterly_contracts

ET = ZoneInfo("America/New_York")
RTH_OPEN, RTH_CLOSE = dt.time(9, 30), dt.time(16, 0)
FULL_SESSION_BARS = 390          # [09:30, 16:00) in 1-min bars
FULL_MIN, EARLY_MIN = 380, 200   # tolerance for missing zero-print minutes


def month_range(start: str, end: str) -> list[str]:
    out, cur = [], dt.date.fromisoformat(start + "-01")
    stop = dt.date.fromisoformat(end + "-01")
    while cur <= stop:
        out.append(cur.strftime("%Y-%m"))
        cur = (cur.replace(day=28) + dt.timedelta(days=5)).replace(day=1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Integrity-check a stitched ES 1-min CSV.")
    ap.add_argument("--data", default="data/es_1min.csv")
    ap.add_argument("--source-tz", default="UTC")
    ap.add_argument("--start-month", default="2025-07")
    ap.add_argument("--end-month", default="2026-06")
    ap.add_argument("--jump-pct", type=float, default=1.0, help="close-to-close jump threshold, percent")
    ap.add_argument("--roll-days", type=int, default=8, help="must match the fetch's --roll-days")
    args = ap.parse_args()

    failures: list[str] = []
    warnings: list[str] = []

    # ---- load RAW (not via load_bars: it dedupes/drops, hiding exactly what
    # this script exists to detect) -------------------------------------------
    raw = pd.read_csv(args.data)
    ts = pd.to_datetime(raw["timestamp"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(ZoneInfo(args.source_tz))
    raw.index = ts.dt.tz_convert(ET)
    raw = raw.sort_index()

    # (d) duplicates / volume --------------------------------------------------
    dups = raw.index[raw.index.duplicated()]
    if len(dups):
        failures.append(f"{len(dups)} duplicate timestamps, e.g. {list(dups[:5])}")

    et_time = raw.index.time
    rth = raw[(et_time >= RTH_OPEN) & (et_time < RTH_CLOSE)]
    neg_vol = rth[rth["volume"] < 0]
    zero_vol = rth[rth["volume"] == 0]
    if len(neg_vol):
        failures.append(f"{len(neg_vol)} RTH bars with NEGATIVE volume: {sorted(set(neg_vol.index.date))[:10]}")
    if len(zero_vol):
        failures.append(f"{len(zero_vol)} RTH bars with ZERO volume: {sorted(set(zero_vol.index.date))[:10]}")

    nan_rows = raw[raw[["open", "high", "low", "close"]].isna().any(axis=1)]
    if len(nan_rows):
        failures.append(f"{len(nan_rows)} rows with NaN OHLC: {sorted(set(nan_rows.index.date))[:10]}")

    # ---- session classification ----------------------------------------------
    sessions: dict[dt.date, dict] = {}
    for day, day_df in rth.groupby(rth.index.normalize()):
        d = day.date()
        first, last = day_df.index[0].time(), day_df.index[-1].time()
        n = len(day_df)
        if first == RTH_OPEN and n >= FULL_MIN:
            kind = "full"
        elif (first == RTH_OPEN and n >= EARLY_MIN
              and dt.time(12, 55) <= last <= dt.time(13, 20)):
            kind = "early_close"
        else:
            kind = "BAD"
        sessions[d] = {"kind": kind, "bars": n, "first": first, "last": last}

    bad = {d: s for d, s in sessions.items() if s["kind"] == "BAD"}
    if bad:
        lines = [f"    {d}: {s['bars']} bars, {s['first']}–{s['last']}" for d, s in sorted(bad.items())]
        failures.append("malformed sessions (missing 09:30 open bar or implausible bar count):\n"
                        + "\n".join(lines))

    # (a) per-month coverage ----------------------------------------------------
    months = month_range(args.start_month, args.end_month)
    per_month: dict[str, dict] = {m: {"full": 0, "early": 0, "bad": 0} for m in months}
    for d, s in sessions.items():
        m = d.strftime("%Y-%m")
        if m in per_month:
            k = {"full": "full", "early_close": "early", "BAD": "bad"}[s["kind"]]
            per_month[m][k] += 1
    for m in months:
        ok = per_month[m]["full"] + per_month[m]["early"]
        if not (18 <= ok <= 23):
            failures.append(f"month {m}: {ok} usable sessions (expected ~20-21)")

    # (c) close-to-close jumps --------------------------------------------------
    dedup = raw[~raw.index.duplicated(keep="last")]
    closes = dedup["close"]
    pct = closes.pct_change().abs() * 100
    jumps = pct[pct > args.jump_pct]

    roll_dates = set()
    span_start = dt.date.fromisoformat(args.start_month + "-01") - dt.timedelta(days=40)
    span_end = dt.date.fromisoformat(args.end_month + "-01") + dt.timedelta(days=70)
    for _, _, active_end in quarterly_contracts(span_start, span_end, args.roll_days):
        roll_dates.add(active_end)  # stitch boundary: first session of the new contract

    jump_rows = []
    for t, p in jumps.items():
        i = dedup.index.get_loc(t)
        prev_t = dedup.index[i - 1]
        same_day = prev_t.date() == t.date()
        near_roll = any(abs((t.date() - r).days) <= 3 for r in roll_dates)
        kind = ("INTRA-SESSION" if same_day
                else "ROLL-BOUNDARY" if near_roll
                else "overnight/weekend")
        jump_rows.append((prev_t, t, p, kind))
        line = (f"{prev_t:%Y-%m-%d %H:%M}->{t:%m-%d %H:%M} ET  {p:.2f}%  [{kind}]  "
                f"{closes[prev_t]:.2f}->{closes[t]:.2f}")
        if kind == "INTRA-SESSION":
            failures.append(f"intra-session jump > {args.jump_pct}%: {line}")
        elif kind == "ROLL-BOUNDARY":
            failures.append(f"possible bad roll stitch: {line}")
        else:
            warnings.append(f"overnight gap > {args.jump_pct}% (can be legitimate news): {line}")

    # ---- report ----------------------------------------------------------------
    print(f"\nData: {args.data}  ({dedup.index[0]:%Y-%m-%d} -> {dedup.index[-1]:%Y-%m-%d}, "
          f"{len(raw):,} rows, {len(sessions)} RTH sessions)\n")
    print(f"{'month':<9} {'full':>5} {'early':>6} {'bad':>4}   verdict")
    for m in months:
        c = per_month[m]
        ok = c["full"] + c["early"]
        v = "ok" if (18 <= ok <= 23 and c["bad"] == 0) else "FAIL"
        print(f"{m:<9} {c['full']:>5} {c['early']:>6} {c['bad']:>4}   {v}")
    extra = {d.strftime('%Y-%m') for d in sessions} - set(months)
    if extra:
        print(f"(+ sessions outside the required range in: {', '.join(sorted(extra))})")

    print(f"\njumps > {args.jump_pct}%: "
          f"{sum(1 for r in jump_rows if r[3] == 'INTRA-SESSION')} intra-session, "
          f"{sum(1 for r in jump_rows if r[3] == 'ROLL-BOUNDARY')} roll-boundary, "
          f"{sum(1 for r in jump_rows if r[3] == 'overnight/weekend')} overnight/weekend")

    if warnings:
        print("\nWARNINGS (not fatal):")
        for w in warnings:
            print(f"  ~ {w}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  x {f}")
        print("\nVERDICT: FAIL — fix the data before backtesting.")
        sys.exit(1)
    print("\nVERDICT: PASS")


if __name__ == "__main__":
    main()
