"""Round-2 selection & one-shot holdout runner (research/ROUND2_PREREG.md).

Mechanically enforces the pre-registered protocol:
- EVERY backtest (IS and OOS) runs at doubled slippage (2 ticks/side),
  overriding whatever the config says;
- the IS/OOS split is by pre-registered DATE, not a fraction;
- in-sample: rank the 12 pre-registered variants, apply family eligibility
  (net > 0, PF >= 1.25, >= 40 decided IS trades), emit AT MOST one pick per
  family (max 3);
- the holdout is evaluated ONLY when --holdout is passed, ONLY for the picks,
  under the six-criterion profit gate. One invocation = the one look.

    # safe: in-sample selection only, holdout untouched
    python scripts/round2_select.py --data data/es_1min_r2.csv --is-end 2025-01-31 --oos-start 2025-02-01

    # THE one shot (prereg: the holdout is consumed by this, whatever the outcome)
    python scripts/round2_select.py --data data/es_1min_r2.csv --is-end 2025-01-31 --oos-start 2025-02-01 --holdout
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.config import ROUND2_FAMILIES, ROUND2_VARIANTS
from orb.data import load_bars, session_slice
from orb.backtest import run_backtest
from orb.metrics import profit_gate, summarize

R2_SLIPPAGE = 2.0   # prereg §1: doubled slippage everywhere in round 2
IS_MIN_DECIDED = 40
IS_MIN_PF = 1.25


def rth_sessions(df) -> int:
    return sum(
        1 for _, day_df in df.groupby(df.index.normalize())
        if len(session_slice(day_df, "09:30", "16:00"))
    )


def monthly_pnl(trades) -> dict:
    out: dict = defaultdict(float)
    for t in trades:
        out[t.day.strftime("%Y-%m")] += t.pnl_usd
    return dict(sorted(out.items()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Round-2 pre-registered selection/holdout.")
    ap.add_argument("--data", required=True)
    ap.add_argument("--source-tz", default="UTC")
    ap.add_argument("--is-end", required=True, help="last IS date, YYYY-MM-DD (pre-registered)")
    ap.add_argument("--oos-start", required=True, help="first OOS date, YYYY-MM-DD (pre-registered)")
    ap.add_argument("--holdout", action="store_true",
                    help="evaluate the picks on the holdout — THE one look, consumes it")
    args = ap.parse_args()

    is_end = dt.date.fromisoformat(args.is_end)
    oos_start = dt.date.fromisoformat(args.oos_start)
    if is_end >= oos_start:
        sys.exit("error: --is-end must precede --oos-start")

    df = load_bars(args.data, source_tz=args.source_tz)
    is_df = df[df.index.map(lambda ts: ts.date() <= is_end)]
    oos_df = df[df.index.map(lambda ts: ts.date() >= oos_start)]

    print(f"\nRound-2 selection (research/ROUND2_PREREG.md) — slippage forced to "
          f"{R2_SLIPPAGE:.0f} ticks/side")
    print(f"Data: {args.data}")
    print(f"  IS:  {is_df.index[0].date()} -> {is_df.index[-1].date()} ({rth_sessions(is_df)} sessions)")
    print(f"  OOS: {oos_df.index[0].date()} -> {oos_df.index[-1].date()} ({rth_sessions(oos_df)} sessions)"
          f"{'' if args.holdout else '  [NOT touched this run]'}\n")

    # --- in-sample evaluation ------------------------------------------------
    rows = []
    for cfg in ROUND2_VARIANTS:
        forced = dataclasses.replace(cfg, slippage_ticks=R2_SLIPPAGE)
        stats: dict = {}
        trades = run_backtest(is_df, forced, stats=stats)
        m = summarize(cfg.name, trades)
        decided = m.wins + m.losses
        eligible = m.net_usd > 0 and m.profit_factor >= IS_MIN_PF and decided >= IS_MIN_DECIDED
        rows.append((cfg, m, decided, eligible, stats))

    print(f"{'variant':<22} {'net$':>9} {'PF':>5} {'dec':>4} {'exp$':>7} {'win%*':>6} {'skip':>5}  eligible")
    for cfg, m, decided, eligible, stats in sorted(rows, key=lambda r: r[1].expectancy_usd, reverse=True):
        skips = sum(stats.get("skipped", {}).values())
        print(f"{m.name:<22} {m.net_usd:>9,.0f} {m.profit_factor:>5.2f} {decided:>4} "
              f"{m.expectancy_usd:>7.1f} {m.win_rate*100:>5.1f}% {skips:>5}  {'YES' if eligible else '-'}")
    print("(* win rate is reported only — it filters nothing. prereg §1)")

    # --- family picks ----------------------------------------------------------
    by_name = {cfg.name: (cfg, m, decided, eligible) for cfg, m, decided, eligible, _ in rows}
    picks = []
    for family, names in ROUND2_FAMILIES.items():
        members = [by_name[n] for n in names if by_name[n][3]]
        if not members:
            print(f"\nfamily {family}: no eligible variant — no pick (prereg §3)")
            continue
        members.sort(key=lambda r: r[1].expectancy_usd, reverse=True)
        cfg, m, decided, _ = members[0]
        picks.append(cfg)
        print(f"\nfamily {family}: pick = {cfg.name} "
              f"(IS net ${m.net_usd:,.0f}, PF {m.profit_factor:.2f}, exp ${m.expectancy_usd:.1f})")

    if not picks:
        print("\nNo family produced an eligible pick. The holdout was NOT evaluated and")
        print("remains unconsumed. Per prereg §5: report near-misses, extract lessons,")
        print("propose round-three hypotheses WITHOUT testing them on this data.")
        return

    if not args.holdout:
        print(f"\n{len(picks)} pick(s) locked: {', '.join(c.name for c in picks)}")
        print("Holdout NOT touched. Re-run with --holdout for the one pre-committed look.")
        return

    # --- THE holdout look --------------------------------------------------------
    print("\n" + "=" * 78)
    print("HOLDOUT EVALUATION — this consumes the OOS data regardless of outcome")
    print("=" * 78)
    oos_sessions = rth_sessions(oos_df)
    passers = []
    for cfg in picks:
        forced = dataclasses.replace(cfg, slippage_ticks=R2_SLIPPAGE)
        trades = run_backtest(oos_df, forced)
        rep = profit_gate(cfg.name, trades, oos_sessions)
        print(f"\n{rep.line()}")
        print(f"  criteria: net>0={rep.criteria['a']}  PF>=1.25={rep.criteria['b']}  "
              f"decided>=40={rep.criteria['c']}  ex-top5>0={rep.criteria['d']}  "
              f"month<=40%={rep.criteria['e']}  DD<1.5xann={rep.criteria['f']}")
        print("  monthly:", {k: round(v) for k, v in monthly_pnl(trades).items()})
        if rep.passes:
            passers.append(cfg.name)

    print()
    if passers:
        print(f"PASSED the profit gate: {', '.join(passers)}")
        print("Per prereg §5: run the stress gauntlet next; even a full survivor is a")
        print('"candidate for paper trading," never a "validated edge."')
    else:
        print("Zero picks passed the profit gate. Per prereg §5: this was the expected")
        print("outcome. The holdout is now consumed — no re-tests, no added picks.")


if __name__ == "__main__":
    main()
