"""Freeze-period count check — THE ONLY tool used during the round-3 freeze.

Per research/ROUND3_PREREG.md amendments (2026-07-03): fetches NQ 1-min bars
through the latest COMPLETE month, runs the integrity check, then runs only
the two frozen picks and reports per-month decided-trade counts, cumulative
progress toward the 40-trade evaluation threshold, and skip decomposition.

P&L is HARD-SUPPRESSED: nothing derived from trade P&L is printed or written
to disk except win/loss classification counts. The report is assembled in
memory and refused (RuntimeError) if it contains a dollar sign; a unit test
(tests/test_holdout_check.py) locks this. Bar data (prices/volume) is market
data, not P&L, and is cached at data/nq_holdout_accrual.csv.

Filter warmup (pre-committed): the trailing-OR history is seeded from
2026-05-01 — OR sizes only; trades are counted strictly from 2026-07-01.

    python scripts/holdout_count_check.py            # monthly freeze check
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FROZEN_PICKS = ("R3-H2c-bandwide", "R3-H3d-noon-band")
HOLDOUT_START = dt.date(2026, 7, 1)
WARMUP_START = dt.date(2026, 5, 1)       # trailing-OR seeding only
FREEZE_DEADLINE = dt.date(2027, 3, 31)   # 9 months -> INSUFFICIENT EVIDENCE
TARGET_DECIDED = 40
R3_SLIPPAGE = 2.0
CACHE = "data/nq_holdout_accrual.csv"


def last_complete_month_end(today: dt.date) -> dt.date:
    return today.replace(day=1) - dt.timedelta(days=1)


def count_summary(trades, holdout_start: dt.date = HOLDOUT_START) -> dict:
    """Reduce trades to COUNTS only. P&L is read solely to classify
    decided-vs-scratch; no monetary value survives into the result."""
    per_month: dict = defaultdict(int)
    total = 0
    for t in trades:
        if t.day < holdout_start:
            continue  # warmup-window trades are never counted or reported
        if t.pnl_usd != 0:
            per_month[t.day.strftime("%Y-%m")] += 1
            total += 1
    return {"per_month": dict(sorted(per_month.items())), "decided_total": total}


def build_report(as_of: dt.date, pick_counts: dict, pick_skips: dict) -> str:
    """Assemble the freeze report from counts/skips ONLY, then refuse to
    return it if any dollar figure slipped in."""
    months_elapsed = ((as_of.year - HOLDOUT_START.year) * 12
                      + as_of.month - HOLDOUT_START.month + 1)
    lines = []
    lines.append("=== ROUND-3 FREEZE COUNT CHECK (P&L suppressed) ===")
    lines.append(f"as of end of month: {as_of:%Y-%m}   "
                 f"months elapsed: {months_elapsed}/9   "
                 f"deadline: {FREEZE_DEADLINE} -> INSUFFICIENT EVIDENCE")
    for name in FROZEN_PICKS:
        c = pick_counts[name]
        lines.append("")
        lines.append(f"--- {name}")
        lines.append(f"  decided trades per month: {c['per_month'] or '(none yet)'}")
        lines.append(f"  cumulative decided: {c['decided_total']}/{TARGET_DECIDED}")
        lines.append(f"  skip decomposition (incl. warmup window, where the band "
                     f"filter is closed by design): {pick_skips[name]}")
        if c["decided_total"] >= TARGET_DECIDED:
            lines.append(f"  >>> EVALUATION DUE: {TARGET_DECIDED}+ decided trades exist. "
                         "Do NOT evaluate with this tool — the one-look evaluation is a "
                         "separate deliberate act per the prereg (skip decomposition and "
                         "denominator review first; roll-date rule applies).")
        else:
            lines.append(f"  evaluation not yet due ({TARGET_DECIDED - c['decided_total']} "
                         "more decided trades needed).")
    report = "\n".join(lines)
    if "$" in report:
        raise RuntimeError("P&L suppression violated: report contains a dollar figure")
    return report


def main() -> None:
    today = dt.date.today()
    as_of = last_complete_month_end(today)
    if as_of < dt.date(HOLDOUT_START.year, HOLDOUT_START.month + 1, 1) - dt.timedelta(days=1):
        print("No complete accrued month yet: the holdout began "
              f"{HOLDOUT_START} and the first check is possible after "
              f"{dt.date(HOLDOUT_START.year, HOLDOUT_START.month + 1, 1) - dt.timedelta(days=1)}.")
        return

    from orb.config import NQ, ROUND3_VARIANTS
    from orb.data import load_bars
    from orb.backtest import run_backtest
    from orb.ibkr import fetch_es_stitched, save_csv

    print(f"fetching NQ {WARMUP_START} -> {as_of} (warmup from {WARMUP_START}; "
          f"counts strictly from {HOLDOUT_START})")
    df_raw = fetch_es_stitched(start=WARMUP_START.isoformat(), end=as_of.isoformat(),
                               symbol="NQ", port=4001)
    save_csv(df_raw, CACHE)

    first_month = f"{HOLDOUT_START:%Y-%m}"
    check = subprocess.run(
        [sys.executable, "scripts/check_data.py", "--data", CACHE,
         "--start-month", first_month, "--end-month", f"{as_of:%Y-%m}"],
        capture_output=True, text=True)
    print(check.stdout)
    if check.returncode != 0:
        print("integrity FAIL — stopping per protocol; present the failures for "
              "sign-off before any count is trusted.")
        sys.exit(1)

    df = load_bars(CACHE, source_tz="UTC")
    pick_counts, pick_skips = {}, {}
    for name in FROZEN_PICKS:
        cfg = next(v for v in ROUND3_VARIANTS if v.name == name)
        forced = dataclasses.replace(cfg, slippage_ticks=R3_SLIPPAGE,
                                     commission_per_side=NQ.commission_per_side)
        stats: dict = {}
        trades = run_backtest(df, forced, spec=NQ, stats=stats)
        pick_counts[name] = count_summary(trades)
        pick_skips[name] = stats.get("skipped", {})

    print(build_report(as_of, pick_counts, pick_skips))


if __name__ == "__main__":
    main()
