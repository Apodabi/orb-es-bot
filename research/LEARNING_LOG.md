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
