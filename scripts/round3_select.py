"""Round-3 selection & one-shot holdout runner (research/ROUND3_PREREG.md) — NQ.

Mechanically enforces the pre-registered protocol:
- instrument = NQ ($20/point, $2.45/side all-in); EVERY backtest runs at
  doubled slippage (2 ticks/side), overriding the config;
- IS/OOS split by the pre-registered dates (§e, inserted at fetch time);
- ≤ 1 pick per family by IS expectancy among eligible members, ≤ 3 total;
- the holdout is evaluated ONLY with --holdout, ONLY for the picks, under the
  round-2 profit gate PLUS the benchmark-alpha criterion (g): OOS net must
  exceed naive long-at-open/flat-at-close on the variant's own trading days
  at the same costs. One invocation = the one look.

    python scripts/round3_select.py --data data/nq_1min_r3.csv --is-end <date> --oos-start <date>
    ...same + --holdout       # THE one look; consumes the holdout regardless of outcome
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.config import NQ, ROUND3_FAMILIES, ROUND3_VARIANTS
from orb.data import load_bars, session_slice
from orb.backtest import run_backtest
from orb.metrics import profit_gate, summarize

R3_SLIPPAGE = 2.0
IS_MIN_DECIDED = 40
IS_MIN_PF = 1.25


def _force(cfg):
    """Prereg costs: doubled slippage + NQ all-in commission."""
    return dataclasses.replace(cfg, slippage_ticks=R3_SLIPPAGE,
                               commission_per_side=NQ.commission_per_side)


def rth_sessions(df) -> int:
    return sum(1 for _, day_df in df.groupby(df.index.normalize())
               if len(session_slice(day_df, "09:30", "16:00")))


def monthly_pnl(trades) -> dict:
    out: dict = defaultdict(float)
    for t in trades:
        out[t.day.strftime("%Y-%m")] += t.pnl_usd
    return dict(sorted(out.items()))


def benchmark_usd_for(oos_df, days, cfg) -> float:
    """Naive benchmark (prereg §c-g): long 1 contract at the 09:30 open
    (+slippage), flat at the close (-slippage), commissions both sides,
    summed over exactly the given trading days."""
    slip = R3_SLIPPAGE * NQ.tick_size
    total = 0.0
    for day, day_df in oos_df.groupby(oos_df.index.normalize()):
        if day.date() not in days:
            continue
        sess = session_slice(day_df, cfg.session_open, cfg.session_close)
        if sess.empty:
            continue
        entry = float(sess["open"].iloc[0]) + slip
        exit_ = float(sess["close"].iloc[-1]) - slip
        total += (exit_ - entry) * NQ.point_value - 2 * NQ.commission_per_side
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="Round-3 pre-registered selection/holdout (NQ).")
    ap.add_argument("--data", required=True)
    ap.add_argument("--source-tz", default="UTC")
    ap.add_argument("--is-end", required=True, help="last IS date, YYYY-MM-DD (prereg §e)")
    ap.add_argument("--oos-start", required=True, help="first OOS date, YYYY-MM-DD (prereg §e)")
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

    print(f"\nRound-3 selection (research/ROUND3_PREREG.md) — NQ, slippage forced to "
          f"{R3_SLIPPAGE:.0f} ticks/side, ${NQ.commission_per_side}/side all-in")
    print(f"Data: {args.data}")
    print(f"  IS:  {is_df.index[0].date()} -> {is_df.index[-1].date()} ({rth_sessions(is_df)} sessions)")
    print(f"  OOS: {oos_df.index[0].date()} -> {oos_df.index[-1].date()} ({rth_sessions(oos_df)} sessions)"
          f"{'' if args.holdout else '  [NOT touched this run]'}\n")

    rows = []
    for cfg in ROUND3_VARIANTS:
        stats: dict = {}
        trades = run_backtest(is_df, _force(cfg), spec=NQ, stats=stats)
        m = summarize(cfg.name, trades)
        decided = m.wins + m.losses
        eligible = m.net_usd > 0 and m.profit_factor >= IS_MIN_PF and decided >= IS_MIN_DECIDED
        rows.append((cfg, m, decided, eligible, stats))

    print(f"{'variant':<20} {'net$':>9} {'PF':>5} {'dec':>4} {'exp$':>7} {'win%*':>6} {'skip':>5}  eligible")
    for cfg, m, decided, eligible, stats in sorted(rows, key=lambda r: r[1].expectancy_usd, reverse=True):
        skips = sum(stats.get("skipped", {}).values())
        print(f"{m.name:<20} {m.net_usd:>9,.0f} {m.profit_factor:>5.2f} {decided:>4} "
              f"{m.expectancy_usd:>7.1f} {m.win_rate*100:>5.1f}% {skips:>5}  {'YES' if eligible else '-'}")
    print("(* win rate is reported only — it filters nothing)")

    by_name = {cfg.name: (cfg, m, decided, eligible) for cfg, m, decided, eligible, _ in rows}
    picks = []
    for family, names in ROUND3_FAMILIES.items():
        members = [by_name[n] for n in names if by_name[n][3]]
        if not members:
            print(f"\nfamily {family}: no eligible variant — no pick (prereg §d)")
            continue
        members.sort(key=lambda r: r[1].expectancy_usd, reverse=True)
        cfg, m, decided, _ = members[0]
        picks.append(cfg)
        print(f"\nfamily {family}: pick = {cfg.name} "
              f"(IS net ${m.net_usd:,.0f}, PF {m.profit_factor:.2f}, exp ${m.expectancy_usd:.1f})")

    if not picks:
        print("\nNo family produced an eligible pick. The holdout was NOT evaluated and")
        print("remains unconsumed. Per prereg §g: report near-misses, extract lessons,")
        print("and apply the §f stopping rule.")
        return

    if not args.holdout:
        print(f"\n{len(picks)} pick(s) locked: {', '.join(c.name for c in picks)}")
        print("Holdout NOT touched. Re-run with --holdout for the one pre-committed look.")
        return

    print("\n" + "=" * 78)
    print("HOLDOUT EVALUATION — this consumes the OOS data regardless of outcome")
    print("=" * 78)
    oos_sessions = rth_sessions(oos_df)
    passers = []
    for cfg in picks:
        trades = run_backtest(oos_df, _force(cfg), spec=NQ)
        days = {t.day for t in trades}
        bench = benchmark_usd_for(oos_df, days, cfg)
        rep = profit_gate(cfg.name, trades, oos_sessions, benchmark_usd=bench)
        print(f"\n{rep.line()}")
        print(f"  criteria: net>0={rep.criteria['a']}  PF>=1.25={rep.criteria['b']}  "
              f"decided>=40={rep.criteria['c']}  ex-top5>0={rep.criteria['d']}  "
              f"month<=40%={rep.criteria['e']}  DD<1.5xann={rep.criteria['f']}  "
              f"beats-benchmark={rep.criteria['g']}")
        print(f"  benchmark (long open->close, same {len(days)} days, same costs): ${bench:,.0f}")
        print("  monthly:", {k: round(v) for k, v in monthly_pnl(trades).items()})
        if rep.passes:
            passers.append(cfg.name)

    print()
    if passers:
        print(f"PASSED the full gate incl. benchmark alpha: {', '.join(passers)}")
        print('Per prereg §g: stress gauntlet next; at best "candidate for paper')
        print('trading," never "validated edge."')
    else:
        print("Zero picks passed. Per prereg §f: the intraday range-breakout program")
        print("on index futures is concluded at the honest null. The holdout is")
        print("consumed — no re-tests, no added picks, no exceptions.")


if __name__ == "__main__":
    main()
