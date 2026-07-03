# Learning Log — ORB on ES

Every research round starts by reading this file. Append after every verdict,
win or lose. Entries are evidence, dates, and the rule we extracted — no spin.

---

## Round 1 — 2025-07 → 2026-06 stitched ES 1-min (verdict 2026-07-02: zero survivors)

1. **Win-rate gates select for fragile shapes.** The 60% gate structurally
   favored small-target/high-win configs. The only "passers" won 63–65% of
   trades and still lost money. The gate and durable trade shapes were
   mutually exclusive: 2R-target profiles win ~39% by design and could never
   pass, yet were the only near-breakeven full-year profiles.
2. **Loss asymmetry killed the pick.** `OR45_b2_range_0.5R`: IS 63.5% win,
   +$5,782 → OOS 58.7% win, −$20,000 over 75 trades. Avg OOS win $750 vs avg
   loss $1,709. Winning often means nothing when losses are 2.3× wins.
3. **P&L was regime-concentrated.** IS profit lived in 3 of 8.5 months
   (Oct/Jan/Feb); OOS decayed monotonically to a −$12.3k June. Breakout P&L
   tracks volatility/trend regimes; chop is not survivable, only avoidable.
4. **Holdout discipline held and was worth it.** Ten predefined variants were
   all IS-ineligible, so that holdout track was never consumed. Exactly one
   sweep pick got the 2025-26 holdout, failed, and the dataset was declared
   consumed. The old multi-shot flow would have "found" a robust config among
   15 leaderboard looks.
5. **Deliberately pessimistic simulation changed answers.** Entry-bar stops,
   gap-through fills, and prev-bar filters (fixed 2026-07-01) turned several
   apparent edges into losses. The optimistic sim was manufacturing edge.

**Rule extracted for round 2:** gate on profitability + distribution
(PF, concentration, drawdown), never on win rate; hypotheses must target
asymmetry, regime, and trend-alignment — the three failure axes above.

---

## Round 2 — pre-registered 2026-07-03 (`ROUND2_PREREG.md`)

*(verdict pending — append results here regardless of outcome)*

---

## Round 2 — 2024-06 → 2025-06 stitched ES 1-min, profit gate (verdict 2026-07-03: zero survivors)

Protocol: pre-registered gate/hypotheses/split (commit 519888d) before the
fetch; 12 variants, 3 pre-committed picks, one holdout look. IS (2024-06 →
2025-01, calm uptrend): every H1/H2 shape net-positive at doubled slippage.
OOS (2025-02 → 2025-06, selloff + tariff shock): all three picks negative.

1. **The regime filter backfired — directionally wrong, not just weak.**
   H2a (trade only when OR >= 20-day median) was the best IS pick ($238/trade)
   and the WORST holdout failure (-$9,712 on 40 trades, 30% win, -$7,178 in
   June alone). A big opening range does not predict follow-through; it often
   means the move already happened. Round one taught "chop kills"; round two
   adds "so does chaos" — OR size selects for post-shock exhaustion days, not
   clean trends.
2. **VWAP alignment is redundant in trend, mildly protective in chaos.** On IS
   data the filter never blocked a single trade (breakouts above the whole OR
   are almost always above VWAP). OOS it did bind, cutting losses from -$3,360
   (H1b) to -$1,322 (H3b) — a loss-reducer in violent tape, nowhere near an
   edge, and vacuous exactly when strategies are winning.
3. **Asymmetric targets survived better but still lost.** The 2R+trailing
   shapes lost 2-7x less than round one's small-target pick did in its OOS,
   with the smallest drawdowns. Loss asymmetry was the right diagnosis; fixing
   it alone is insufficient.
4. **IS -> OOS reversal is now 2-for-2 across regimes.** Whatever in-sample
   profit these shapes show is regime beta, not strategy alpha. Two consumed
   holdouts agree.
5. **The mechanics held under pressure.** Boundary cases resolved by rule, not
   judgment (H1a missed eligibility at PF 1.2496 vs >= 1.25; H2a passed the
   40-trade floor with zero margin). The integrity gate caught a real stitch
   bug (UTC-midnight cutover) BEFORE selection. Win rate filtered nothing.

**Round-three hypotheses (proposed, deliberately UNTESTED on any consumed data):**
- Band regime filter: skip both the bottom AND top OR-size quantiles (chop
  kills and chaos kills; trade the middle).
- Follow-through conditioning: classify the open (gap-and-go vs gap-fade)
  before arming, instead of sizing the range.
- Time-based exits (e.g., flat by 12:00) replacing fixed R targets.
- Honest null: ORB on ES may carry no retail-accessible edge at 1-min
  granularity; consider a structurally different idea before a round three.

> "A strategy that fails honestly is worth more than one that passes dishonestly."
