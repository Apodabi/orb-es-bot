# Round 2 Pre-Registration — ORB on ES futures

**Status: LOCKED at commit time. Committed before any round-two data was fetched;
git history is the proof. Nothing below may be changed after the fetch except by
abandoning round two entirely and starting a round three with fresh data.**

Round one (2025-07 → 2026-06 stitched ES 1-min, consumed 2026-07-02) concluded:
zero survivors. The 60% win-rate gate selected small-target/high-win shapes whose
loss asymmetry (avg OOS win $750 vs avg loss $1,709) destroyed them out-of-sample.
That dataset is **consumed forever** — nothing may ever be tested, re-tested, or
"promoted" against it. See `research/LEARNING_LOG.md`.

---

## 1. The gate (replaces win rate as a filter)

A config **passes** only if ALL of the following hold on the out-of-sample data.
Win rate is a reported statistic only; it must never filter anything.

All round-two backtests — in-sample AND out-of-sample — run with **doubled
slippage (2 ticks per side)** and full commissions ($2.50/side/contract).

| # | Criterion | Exact definition |
|---|---|---|
| (a) | Net P&L > 0 | Sum of `pnl_usd` over all OOS trades (trades generated at 2-tick slippage) |
| (b) | Profit factor ≥ 1.25 | gross wins / gross losses on OOS trades |
| (c) | ≥ 40 decided OOS trades | decided = wins + losses (scratches excluded) |
| (d) | Concentration (days) | Group P&L by calendar day; remove the 5 highest-P&L days; remaining sum must still be > 0 |
| (e) | Concentration (months) | Group P&L by month; gross = sum of positive months; no single month may exceed 40% of gross. One-positive-month runs fail by construction |
| (f) | Drawdown | Max equity drawdown < 1.5 × annualized OOS profit, where annualized = net × 252 / (OOS trading sessions) |

Implementation: `profit_gate()` in `orb/metrics.py`, **alongside** the legacy
win-rate gate (kept for historical comparison, never again used for selection).
Each criterion gets a unit test with hand-built trade lists before any data is
fetched.

## 2. Hypothesis set — 12 deliberate variants, built from round-one evidence

Round-one clues this set is built on: (i) the only near-breakeven full-year
profile was `OR30-2R-trail40-be1` (2R target, trailing, breakeven; PF 0.97,
smallest drawdown); (ii) small targets died on loss asymmetry; (iii) monthly
P&L was regime-dependent (strong Oct/Jan/Feb, catastrophic June chop).

New backtester features required (all off by default, each unit-tested with
hand-crafted bar sequences BEFORE the fetch): session-VWAP filter (previous-bar
convention, no intrabar lookahead), OR-size-vs-trailing-median filter, and
prior-day ATR percentile filter. Cross-day filters use ONLY prior sessions'
data. OR-median requires ≥ 20 prior measured ORs; ATR percentile requires ≥ 30
prior ATR observations (window = most recent 60); sessions before those minimums
are not traded by filtered variants.

### H1 — Asymmetry (win small less often, but win big): family H1

| Variant | Spec | Round-one evidence |
|---|---|---|
| `R2-H1a-2R-be1` | OR30, range stop, 2R target, breakeven at 1R | The 2R+management shape was the only near-breakeven profile; BE-only isolates the management component |
| `R2-H1b-2R-trail-be` | OR30, range stop, 2R target, BE at 1R + 40-tick trail | Round one's literal best full-year profile (−$1,772, PF 0.97), now judged by an honest gate |
| `R2-H1c-3R-halfstop` | OR30, half-range stop, 3R target, 60-tick trail | Pushes the asymmetry further: risk half the range for 3× — the direct antidote to the $750/$1,709 asymmetry that killed round one |
| `R2-H1d-OR45-2R-be1` | OR45, range stop, 2R target, BE at 1R | OR45 shapes topped the round-one IS grid; test whether the wider range plus asymmetric exits survives honestly |

### H2 — Regime filter (don't survive June-type chop; skip it): family H2 (= H1 × volatility gate)

| Variant | Spec | Round-one evidence |
|---|---|---|
| `R2-H2a-be1-bigOR` | H1a + OR size ≥ 1.0× its 20-day median | June died in compressed, choppy ranges; only trade when the day's range is at least typical |
| `R2-H2b-trail-bigOR` | H1b + OR size ≥ 1.0× 20-day median | Same filter on the best round-one shape |
| `R2-H2c-trail-atr50` | H1b + prior-day ATR(14) ≥ 50th percentile of trailing 60 | Alternative regime proxy: monthly P&L tracked volatility regimes, so gate on realized vol directly |
| `R2-H2d-be1-bigOR125` | H1a + OR size ≥ 1.25× 20-day median | Stronger cut of the same hypothesis — if H2a works, the effect should strengthen, not vanish |
| `R2-H2e-triple` | H1b + OR ≥ 1.0× median + VWAP alignment | The maximal deliberate bet: asymmetry + regime + trend all at once (H1×H2×H3) |

### H3 — Trend alignment (trade breakouts only with the session's flow): family H3 (= H1 × VWAP)

| Variant | Spec | Round-one evidence |
|---|---|---|
| `R2-H3a-be1-vwap` | H1a + longs only above session VWAP, shorts only below | Failed breakouts (the loss side of the asymmetry) are disproportionately counter-trend fades |
| `R2-H3b-trail-vwap` | H1b + VWAP alignment | Same filter on the best round-one shape |
| `R2-H3c-OR45-vwap` | H1d + VWAP alignment | Trend filter on the wider-OR variant |

## 3. Multiple-comparisons rule

- **At most ONE pre-committed pick per family (H1, H2, H3): max 3 holdout shots.**
- In-sample eligibility (2-tick slippage): net > 0, PF ≥ 1.25, ≥ 40 decided IS
  trades. Within each family, the eligible variant with the highest IS
  expectancy per trade is the pick. No eligible variant in a family → that
  family gets **no** pick. No substitutions after seeing any OOS number.
- Plain-language false-positive math, so future-us remembers: with 12 no-edge
  variants, several will look good in-sample by luck. If each pre-committed pick
  has roughly a 3–5% chance of passing all six OOS gate criteria by pure luck,
  then three picks give roughly a **1-in-10 chance that something passes with no
  real edge**. That is why (1) only three configs ever touch the holdout,
  (2) a single marginal pass is treated as probably-luck, and (3) passing earns
  only "candidate for paper trading," never "validated edge."

## 4. Test data plan

**Primary plan:** stitched, roll-correct ES 1-min bars 2023-01-01 → 2024-12-31
(`scripts/fetch_data.py --start 2023-01-01 --end 2024-12-31`), IS = 2023,
OOS holdout = 2024.

**Pre-registered fallback (availability-triggered only):** IBKR serves expired
futures history only ~2 years past expiry, so the 2023 and early-2024 contracts
are expected to be unavailable as of 2026-07. If the primary fetch fails for
those windows, the fallback dataset is the maximal IB-available window disjoint
from the consumed round-one data: **2024-06-13 → 2025-06-30**, with
IS = 2024-06-13 → 2025-01-31 and OOS holdout = 2025-02-01 → 2025-06-30.
The fallback triggers ONLY on data availability, never on results. The 40-trade
OOS minimum stays even though the shorter holdout makes it harder to meet —
too-few-trades is a fail, not an excuse.

Integrity gate before any backtest (`scripts/check_data.py`): sessions per
month, exact 09:30 ET opens, roll-boundary jump scan, duplicates, volume checks.
FAIL stops the round.

**The holdout is touched exactly once — one evaluation of ≤ 3 pre-committed
picks — and is consumed regardless of outcome.**

## 5. Expected outcomes (written before the data)

- **The single most likely outcome is zero survivors again.** Intraday breakout
  patterns on ES are among the most heavily arbitraged phenomena in the most
  liquid equity-index future on earth.
- A marginal pass is MORE likely to be luck than edge (see §3 math).
- Pre-agreed responses:
  - **If zero pass:** report near-misses descriptively; extract what the failure
    pattern teaches (as round one taught loss asymmetry); propose round-three
    hypotheses WITHOUT testing them on this data; update the learning log.
  - **If 1+ pass:** run the stress gauntlet — tripled slippage (3 ticks/side),
    OR window shifted ±1 minute, IS/OOS boundary moved one month earlier,
    per-regime monthly breakdown — and even a full survivor is labeled
    **"candidate for paper trading," never "validated edge."**

> "A strategy that fails honestly is worth more than one that passes dishonestly."

## 6. Standing refusals (quote these back at whoever asks, including the author)

1. No peeking at the holdout before the pre-committed picks are locked.
2. No "just one more" pick after any OOS number is seen.
3. Nothing is ever re-tested against the consumed 2025-07→2026-06 dataset.
4. No gate criterion is softened because something "almost" passed.
5. Zero survivors is a valid, useful, expected result — report it plainly.

## 7. Engineering notes (pre-committed scope)

- The VWAP filter is implemented in BOTH the backtester and the live engine,
  with replay parity extended to a VWAP variant.
- The cross-day filters (OR-median, ATR percentile) are implemented in the
  backtester for selection/holdout. The live engine refuses (raises) configs
  using them until live support with parity is built — which is required
  BEFORE any filtered candidate may be paper traded. This avoids silently
  breaking the backtest/live parity guarantee.

---

## Amendments log (provenance only — gate, hypotheses, and split unchanged)

**2026-07-03 — fallback data plan activated (availability-triggered, §4).**
The primary 2023-2024 fetch failed as anticipated: IBKR could not qualify the
2023 contracts (2-year expired-contract retention). Dataset in force:
`data/es_1min_r2.csv`, 2024-06-13 -> 2025-06-30, IS ends 2025-01-31, holdout
begins 2025-02-01.

**2026-07-03 — stitch cutover fix (data construction, pre-selection).**
The integrity gate caught the UTC-midnight window cut placing the quarterly
contract spread between adjacent overnight bars. Cutover moved to 17:00 ET in
the maintenance halt (commit ec3ac9f); refetched; re-checked. No strategy,
gate, or split decision changed.

**2026-07-03 — accepted data exceptions (user sign-off, post-refetch check).**
- (A) One zero-volume RTH minute on 2025-02-17 (Presidents' Day half-session);
  flat bar, correct prices, volume unused by the backtester.
- (B) Thirteen >1% intra-session 1-minute moves, all within the 2025-04-06 ->
  2025-04-09 tariff-shock window (documented historic volatility; consecutive
  prints with continuation). Accepted as REAL market history and deliberately
  retained in the holdout — sanitizing them would bias the test in our favor.
No other integrity failures remain; roll stitches verified clean (0 bad-stitch).

**2026-07-03 — HOLDOUT CONSUMED.** One look, three pre-committed picks, zero
passed the profit gate (H1b: -$3,360 PF 0.84; H2a: -$9,712 PF 0.78; H3b:
-$1,322 PF 0.93). Per §5: near-misses reported descriptively, lessons logged,
round-three hypotheses proposed WITHOUT testing. This dataset joins 2025-07 ->
2026-06 as consumed forever.
