# orb-es-bot

Backtesting toolkit **and live/paper executor** for an **Opening Range Breakout
(ORB)** strategy on **ES futures**, using the first 30 minutes of the New York
regular session (09:30–10:00 ET) as the opening range.

Backtest-first by design: the goal is to race multiple ORB variants and only
promote one to live (Interactive Brokers) once it clears a **≥60% win-rate
gate** on real, out-of-sample ES data — and paper-trades cleanly.

## How it works

1. The **opening range** = high/low of the first `or_minutes` (default 30) of the session.
2. After the OR window, arm breakout stop orders `entry_buffer_ticks` beyond each side.
3. On a breakout, set a stop (opposite side of the range / fixed / fraction) and a
   target (R-multiple / fixed / range-multiple).
4. Flatten at the session close if still open.

All behaviour is driven by `StrategyConfig` (`orb/config.py`). The live executor
(`orb/live.py`) is driven by the **same config through the same helper functions**
as the backtester, and `tests/test_strategy.py` asserts trade-for-trade parity
between the two on identical data.

## Quickstart (Windows PowerShell)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1     # or: uv venv
pip install -r requirements.txt

python scripts\make_sample.py          # synthetic ES bars (plumbing only!)
python tests\test_strategy.py          # 19 tests: entry/exit logic + live-engine parity
python scripts\compare.py              # race all variants vs the 60% gate
python scripts\run_backtest.py --variant OR30-range-1R --trades   # one variant + trade log
python scripts\validate.py             # IS/OOS robustness (selection is IS-only)
python scripts\sweep.py                # 64-config grid sweep, one pre-committed OOS test
```

macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate`, then the same
scripts with forward slashes.

## Getting real ES data

**No account needed (fastest):** Yahoo Finance serves ~30 days of real ES 1-min bars.

```powershell
python scripts\fetch_yf.py --out data\es_yf_1min.csv
python scripts\compare.py --data data\es_yf_1min.csv
```

**Interactive Brokers (for serious validation):** start TWS or IB Gateway with the
API enabled (paper port 7497), then fetch a **roll-correct stitched** history —
each quarterly contract contributes only its front-month window:

```powershell
python scripts\fetch_data.py --start 2026-01-01 --end 2026-07-01 --out data\es_1min.csv
python scripts\compare.py --data data\es_1min.csv
```

(A single-contract fetch `--expiry 202609 --duration "30 D"` is also available,
but a 60-day pull from one expiry spans the quarterly roll and mixes in thin
back-month sessions — the script warns about this.)

Every data source in this repo (both fetchers AND `make_sample.py`) emits UTC
timestamps, and every script defaults to `--source-tz UTC` — no flag needed.
Only for a foreign CSV with exchange-local timestamps would you pass
`--source-tz America/New_York`. The toolkit converts to ET internally for all
session logic.

## Validation philosophy

Win rate alone is gameable, and any single backtest period can be curve-fit. So:

- **`validate.py`** splits the history chronologically (default 70/30), ranks
  variants **on the in-sample period only**, and evaluates exactly ONE
  pre-committed pick out-of-sample. Testing the whole leaderboard on the holdout
  and keeping what sticks is curve-fitting with extra steps.
- **`sweep.py`** searches a 64-config grid in-sample and gives only the single
  top survivor one shot at the holdout. Both scripts warn you when a holdout has
  been consumed — fetch fresh data before re-validating tweaked configs.
- The backtester's fill model is deliberately **pessimistic**: entry-bar stops
  and targets are honored (conservatively on ambiguous bars), stops gap-fill at
  the open, stops/targets round to the tick grid against you, and the EMA filter
  only uses the previous bar's close (no intrabar lookahead). If it looks good
  here, it had a chance; if it only looks good in an optimistic sim, it never did.

## Paper / live trading

```powershell
# replay the LIVE engine over a CSV — no broker, see exactly what it would do
python scripts\run_live.py --replay data\es_yf_1min.csv --source-tz UTC --variant OR30-range-1R

# paper-trade against TWS paper (port 7497); start BEFORE 09:30 ET
python scripts\run_live.py --variant OR30-range-1R --qty 1

# watch decisions without sending any orders
python scripts\run_live.py --variant OR30-range-1R --dry-run

# micro contract (1/10th the size) — the sane first step
python scripts\run_live.py --symbol MES --variant OR30-range-1R
```

The executor rests breakout stop orders at the exchange as an OCA pair, each
with attached stop-loss + take-profit children (server-side protection — you are
never in a position without a working stop, even if the bot dies). Breakeven and
trailing tighten the child stop; unfilled entries are cancelled at
`no_entry_after` and at the close; positions flatten at the close. A
`--max-daily-loss` kill switch (default $1,000) stops the day after that much
realized loss. Trades append to `data\live_trades.csv`.

**Real money requires all three:** a live port (`--port 7496`), the `--live`
flag, and typing a confirmation. Don't — until a variant is robust on real
out-of-sample data and has paper-traded cleanly for weeks.

## Strategy features (configurable in `StrategyConfig`)

| Field | Effect |
|---|---|
| `ema_trend_filter` | Only take longs above / shorts below an intraday EMA of this span (previous bar's close) |
| `no_entry_after` | Block new entries after this ET time (e.g. `"12:00"`) |
| `breakeven_at_r` | Move stop to entry once price reaches this R multiple |
| `trailing_stop_ticks` | Trail the stop this many ticks behind the best price |
| `stop_type` / `target_type` | `range` / `fixed` / `fraction` stops; `r_multiple` / `fixed` / `range_multiple` targets |
| `conservative_fills` | Ambiguous bars (stop AND target touched) resolve to the stop |

## Layout

```
orb/
  config.py     contract spec (ES: tick 0.25, $50/pt) + StrategyConfig + VARIANTS
  data.py       CSV loader, tz handling, session slicing, data hygiene
  backtest.py   event-driven bar-by-bar simulator -> list[Trade]
  metrics.py    win rate, profit factor, expectancy, drawdown, avg R, 60% gate
  ibkr.py       ib_insync historical fetch: front-month resolution, roll-correct stitching
  live.py       OrbSessionEngine (decisions) + ReplayRunner (parity) + IbkrRunner (orders)
scripts/        make_sample, fetch_yf, fetch_data, run_backtest, compare, validate, sweep, run_live
tests/          19 tests: hand-crafted bar sequences + engine/backtest parity
```

## Cost model

Each fill takes adverse `slippage_ticks` and pays `commission_per_side` per
contract. ES tick = 0.25 pt = $12.50; point value = $50 (MES: $5).

## ⚠️ Status & caveats

- **Synthetic data proves only the plumbing.** A random walk is net-negative
  after costs by construction — never read edge into it. Validate on real ES bars.
- Yahoo data is ~30 days — enough to smoke-test, far too short to validate an
  edge. Use the stitched IBKR fetch for months of roll-correct history.
- Win rate is necessary but **not sufficient**: a high-win-rate, small-target
  variant can still be net-negative (watch profit factor and expectancy too).
  `validate.py` enforces both.
- Always validate **out-of-sample** and paper-trade before risking capital.
- `ib_insync` is community-archived (its author passed away in 2024); the
  `ib_async` fork is the maintained successor with the same API if you ever need
  to switch.
- This is research tooling, **not financial advice**.
