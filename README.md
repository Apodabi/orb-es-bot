# orb-es-bot

Backtesting toolkit for an **Opening Range Breakout (ORB)** strategy on **ES
futures**, using the first 30 minutes of the New York regular session
(09:30–10:00 ET) as the opening range.

Backtest-first by design: the goal is to race multiple ORB variants and only
promote one to live (Interactive Brokers) once it clears a **≥60% win-rate
gate** on real, out-of-sample ES data.

## How it works

1. The **opening range** = high/low of the first `or_minutes` (default 30) of the session.
2. After the OR window, arm breakout stop orders `entry_buffer_ticks` beyond each side.
3. On a breakout, set a stop (opposite side of the range / fixed / fraction) and a
   target (R-multiple / fixed / range-multiple).
4. Flatten at the session close if still open.

All behaviour is driven by `StrategyConfig` (`orb/config.py`), so the same config
will later drive the live IBKR executor — only the data feed and order placement differ.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # pandas/numpy; ib_insync only for real data

python scripts/make_sample.py          # synthetic ES bars (plumbing only!)
python tests/test_strategy.py          # unit tests for the entry/exit logic
python scripts/compare.py              # race all variants vs the 60% gate
python scripts/run_backtest.py --variant OR30-range-1R --trades   # one variant + trade log
```

## Using real ES data (Interactive Brokers)

Start TWS or IB Gateway with the API enabled (paper port 7497), then:

```bash
pip install ib_insync
python scripts/fetch_data.py --expiry 20240920 --duration "60 D" --out data/es_1min.csv
python scripts/compare.py --data data/es_1min.csv --source-tz UTC
```

IBKR timestamps are UTC, so pass `--source-tz UTC`; the toolkit converts to ET
internally for all session logic.

## Layout

```
orb/
  config.py     contract spec (ES: tick 0.25, $50/pt) + StrategyConfig + VARIANTS
  data.py       CSV loader, tz handling, session slicing
  backtest.py   event-driven bar-by-bar simulator -> list[Trade]
  metrics.py    win rate, profit factor, expectancy, drawdown, avg R, 60% gate
  ibkr.py       ib_insync historical-data fetch (and the seam for live trading)
scripts/        make_sample, fetch_data, run_backtest, compare
tests/          hand-crafted bar sequences pinning down the logic
```

## Cost model

Each fill takes adverse `slippage_ticks` and pays `commission_per_side` per
contract. ES tick = 0.25 pt = $12.50; point value = $50.

## ⚠️ Status & caveats

- **Synthetic data proves only the plumbing.** A random walk is net-negative
  after costs by construction — never read edge into it. Validate on real ES bars.
- Win rate is necessary but **not sufficient**: a high-win-rate, small-target
  variant can still be net-negative (watch profit factor and expectancy too).
- Always validate **out-of-sample** and paper-trade before risking capital.
- This is research tooling, **not financial advice**.
