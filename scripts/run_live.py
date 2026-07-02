"""Run the ORB engine live (paper by default) or replay it over a CSV.

Replay a config over historical data through the LIVE engine (no broker):
    python scripts/run_live.py --replay data/es_yf_1min.csv --source-tz UTC --variant OR30-range-1R

Paper-trade against TWS/IB Gateway (port 7497 = TWS paper, 4002 = GW paper):
    python scripts/run_live.py --variant OR30-range-1R --qty 1

Watch what it WOULD do without sending orders:
    python scripts/run_live.py --variant OR30-range-1R --dry-run

Live trading is deliberately annoying to enable: it requires --port 7496 (or
4001), the --live flag, AND typing a confirmation. Do not do this until a
variant is robust on real out-of-sample data (scripts/validate.py) and has run
cleanly on paper for a meaningful stretch.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.config import ES, MES, VARIANTS
from orb.metrics import summarize


def pick_variant(name: str | None):
    if not name:
        return VARIANTS[0]
    cfg = next((v for v in VARIANTS if v.name == name), None)
    if cfg is None:
        names = ", ".join(v.name for v in VARIANTS)
        sys.exit(f"error: unknown variant {name!r}. Valid names: {names}")
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser(description="Live/paper ORB executor (or CSV replay).")
    ap.add_argument("--variant", default=None, help="variant name from config.VARIANTS (default: first)")
    ap.add_argument("--replay", default=None, help="CSV path: replay the live engine offline instead of trading")
    ap.add_argument("--source-tz", default="UTC", help="replay only: tz of raw CSV timestamps")
    ap.add_argument("--symbol", default="ES", choices=["ES", "MES"], help="MES = micro (1/10th size) — start there")
    ap.add_argument("--expiry", default=None, help='contract month, e.g. "202609" (default: front month)')
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497, help="7497 TWS paper, 4002 GW paper (7496/4001 live)")
    ap.add_argument("--client-id", type=int, default=42)
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="connect + decide, but never send orders")
    ap.add_argument("--live", action="store_true", help="required (with a live port) to trade real money")
    ap.add_argument("--max-daily-loss", type=float, default=1000.0,
                    help="kill switch: stop trading for the day after this many USD of realized loss (0 = off)")
    ap.add_argument("--log", default="data/live_trades.csv")
    args = ap.parse_args()

    cfg = pick_variant(args.variant)

    if args.replay:
        from orb.data import load_bars
        from orb.live import ReplayRunner

        df = load_bars(args.replay, source_tz=args.source_tz)
        print(f"\nReplaying {args.replay} through the LIVE engine "
              f"({df.index[0].date()} -> {df.index[-1].date()})")
        print(f"Variant: {cfg.name}\n")
        trades = ReplayRunner(cfg, verbose=True).run(df)
        print()
        print(summarize(cfg.name, trades).line())
        print("\nday         dir    entry     exit   reason     R     pnl$")
        for t in trades:
            print(f"{t.day}  {t.direction:<5} {t.entry_price:>8.2f} {t.exit_price:>8.2f}"
                  f"  {t.exit_reason:<7} {t.r_multiple:>5.2f} {t.pnl_usd:>8.1f}")
        return

    if args.live or args.port in (7496, 4001):
        if not args.live:
            sys.exit("Port 7496/4001 is live trading — add --live if you truly mean it.")
        print("!!! LIVE TRADING with real money on port", args.port)
        print(f"    {args.symbol} x{args.qty}, variant {cfg.name}")
        answer = input('Type "I UNDERSTAND THE RISK" to continue: ')
        if answer.strip() != "I UNDERSTAND THE RISK":
            sys.exit("aborted.")

    from orb.live import IbkrRunner

    # MES is $5/point, not ES's $50 — PnL, R and the kill switch depend on it
    spec = MES if args.symbol == "MES" else ES

    runner = IbkrRunner(
        cfg,
        spec,
        symbol=args.symbol,
        expiry=args.expiry,
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        qty=args.qty,
        dry_run=args.dry_run,
        allow_live=args.live,
        max_daily_loss_usd=args.max_daily_loss,
        log_path=args.log,
    )
    runner.run()


if __name__ == "__main__":
    main()
