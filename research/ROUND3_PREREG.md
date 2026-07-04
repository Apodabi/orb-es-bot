# Round 3 Pre-Registration — ORB on NQ futures

**Status: LOCKED at commit time, BEFORE any NQ data was fetched (a one-off
subscription smoke test runs after this commit and its bars are discarded).
Git history is the proof. Nothing below may be changed after the research
fetch except the §5 date insertion it explicitly provides for.**

Built from `research/LEARNING_LOG.md` rounds 1–2 (both zero survivors). The
consumed datasets — ES 2025-07→2026-06 and ES 2024-06→2025-06 — remain
**permanently off-limits** for any testing, tuning, or "confirmation."

## (a) Instrument scope

**NQ only this round** (CME E-mini Nasdaq-100: tick 0.25 = $5, $20/point,
all-in ~$2.45/side per the IBKR schedule as of 2026-07; MNQ ~$0.62/side noted
for reference only). ES rejoins only in a future round once ≥ 6 fresh months
have accrued beyond 2026-06. No instruments may be added mid-round. Slippage
stays specified in ticks: doubled = 2 ticks/side on every round-3 backtest.

**Known limitation, stated up front:** IBKR retention means the NQ history
will overlap 2024-06→2026-07 — the same *calendar* regimes as the consumed ES
rounds (different instrument, never tested). We know from those rounds which
months were calm or violent for equity futures generally. Mitigations: the
hypothesis set below derives only from documented learning-log lessons, the
variant list is locked here, and selection is mechanical (IS expectancy rank).
The residual risk (hypotheses indirectly shaped by regime knowledge) is
accepted and disclosed, and is one more reason a marginal pass reads as luck.

## (b) Hypotheses — three families from the learning log, 12 variants

Base management shape carried from round 2's most durable loser: range stop,
breakeven at 1R, 40-tick trail (2R target where a target applies). Round-2
lessons in force: first-touch entries buy exhaustion (#1); extreme OR sizes are
hostile in both directions (R1 #3 + R2 #1); exit geometry never yet tested (#3).

**New features (Part 3; all off by default; hand-built-bar unit tests
committed before the fetch):**
- *Follow-through entry* — `confirm_closes=N`: entry requires N consecutive
  1-min closes strictly beyond the OR boundary (count resets on a close back
  inside); alternatively `confirm_beyond_ticks=x`: one close ≥ OR-high + x
  ticks (≤ OR-low − x for shorts). Either replaces first-touch stop entries:
  fill at the NEXT bar's open ± slippage (market order after confirmation).
  Direction/EMA/VWAP permissions are evaluated at the confirming close
  (previous-bar convention, no intrabar lookahead).
- *Band regime filter* — `or_pctile_min/max`: trade only when today's OR size
  sits in [min, max] percentile of the trailing 20 measured ORs (inclusive
  rank among priors; requires 20 priors; earlier sessions don't trade).
- *Time exits* — `max_hold_minutes=N`: exit at the OPEN of the first bar
  starting ≥ entry + N minutes; `exit_at_time="HH:MM"`: exit at the OPEN of
  the first bar starting ≥ that ET time; both ∓ slippage, reason "time",
  checked before that bar's stop/target (a market order at the open precedes
  intrabar fills). `target_type="none"` disables the profit target (stop and
  management stay active).

| Variant | Spec (on the base shape unless noted) | Evidence rationale |
|---|---|---|
| `R3-H1a-cc2` | confirm_closes=2, 2R | R2 #1: first-touch bought exhaustion — demand two closes of follow-through before paying up |
| `R3-H1b-cc3` | confirm_closes=3, 2R | Dose-response sibling: if H1a's effect is real it should survive a stricter confirmation, not vanish |
| `R3-H1c-cb8` | confirm_beyond_ticks=8, 2R | Magnitude-based confirmation (2 NQ points beyond the OR) — tests persistence vs distance as the follow-through signal |
| `R3-H1d-cc2-3R` | confirm_closes=2, half-range stop, 3R, 60-tick trail | Confirmation × deeper asymmetry: R2 #3 said asymmetric shapes survive best; combine with the entry fix |
| `R3-H2a-band` | OR pctile 30–70, 2R | R1 #3 + R2 #1: chop kills AND chaos kills — trade only the unremarkable middle of the OR distribution |
| `R3-H2b-band-cc2` | band 30–70 + confirm_closes=2 | H1×H2: both diagnosed failure axes (entry exhaustion, regime extremes) addressed at once |
| `R3-H2c-bandwide` | OR pctile 20–80, 2R | Graceful-degradation sibling: a real band effect should weaken smoothly as the band widens, not flip sign |
| `R3-H2d-band-3R` | band 30–70, half-range stop, 3R, 60-tick trail | Band × asymmetry cross without the entry change, isolating the filter's contribution |
| `R3-H3a-t120` | no target, max_hold_minutes=120, BE at 1R | R2 #3/#4: maybe exits, not entries, are the failure — cap time-risk, let the stop define loss |
| `R3-H3b-noon` | no target, exit_at_time=12:00 | The learning-log proposal verbatim: morning-only exposure, flat before the afternoon regime |
| `R3-H3c-t120-cc2` | H3a + confirm_closes=2 | H1×H3: confirmed entry with time-capped exposure — the full "entry AND exit were both wrong" bet |
| `R3-H3d-noon-band` | H3b + band 30–70 | H2×H3: middle-regime days, morning-only — maximal deliberate combination of the lessons |

## (c) The gate — round-2's six criteria unchanged, plus benchmark alpha

All six round-2 criteria apply verbatim (net > 0 at doubled slippage; PF ≥
1.25; ≥ 40 decided OOS trades; net > 0 after removing the 5 best days; no
month > 40% of gross; DD < 1.5× annualized). **Plus:**

| # | Criterion | Exact definition |
|---|---|---|
| (g) | Benchmark alpha | OOS net P&L must EXCEED the naive benchmark: long 1 contract at the 09:30 open (first RTH bar open + 2-tick slippage), flat at the close (last RTH bar close − 2-tick slippage), commissions both sides, summed over exactly the days the variant traded. Operationalizes R2 #4: "regime beta, not alpha." |

Win rate remains reported-only. Implementation: `profit_gate(...,
benchmark_usd=...)` adds criterion (g); unit-tested with hand-built trades
before the fetch.

## (d) Selection rule

≤ 1 pre-committed pick per family (H1, H2, H3; crosses belong to the family
listed in their name's prefix... explicitly: H2b→H2, H3c→H3, H3d→H3), ≤ 3
total. IS eligibility: net > 0, PF ≥ 1.25, ≥ 40 decided IS trades at doubled
slippage; pick = highest IS expectancy among eligible family members; no
eligible member → no pick for that family. Exactly ONE holdout look,
consumed regardless of outcome. No substitutions after any OOS number exists.

## (e) Split rule — fixed now, dates inserted at fetch time

Fetch the maximum stitched roll-correct NQ 1-min history IBKR retention
allows (expected ≈ 2024-06 → present). **The final 5 calendar months of
whatever range arrives = holdout; everything earlier = in-sample.** The
concrete dates are written into the amendments log below when the fetch
lands, BEFORE any backtest runs. Integrity gate (`check_data.py`) with
FAIL-stop and explicit user sign-off for exceptions, exactly as round 2.

## (f) Stopping rule

If round three returns zero survivors, the intraday range-breakout program on
index futures is concluded at the honest null. Any round four must be either
a structurally different strategy class or wait for ≥ 6 months of genuinely
fresh data — no exceptions, no "one more variant."

## (g) Expected outcomes

**Zero survivors remains the single most likely result** — NQ is the second
most liquid, second most arbitraged equity-index future on earth, and two ES
rounds have already returned the null. That outcome still pays: it cleanly
concludes the program at a defensible null after three pre-registered rounds,
and the learning log compounds either way — the follow-through, band, and
time-exit answers transfer to whatever strategy class comes next. A marginal
pass is more likely luck than edge (≤ 3 picks × ~3–5% joint-luck ≈ 1-in-10);
any passer gets the round-2 stress gauntlet and at best the label "candidate
for paper trading," never "validated edge."

## Hard boundaries (quote back at anyone who asks, including the author)

1. No live or paper trading wiring this round — the executor stays idle; the
   engine refuses round-3 feature configs until parity support exists.
2. Nothing is ever tested against either consumed ES dataset.
3. No gate softening after results exist. 4. No added instruments.
5. No holdout peeking; no picks added after results. 6. Zero survivors is a
   valid, useful, expected result — reported unsoftened, in the round-2 format.

---

## Amendments log (provenance only)

**2026-07-03 — data landed; §e split dates fixed BEFORE any backtest.**
`data/nq_1min_r3.csv`: 725,532 bars, 2024-06-12 -> 2026-07-01 (ET sessions),
528 RTH sessions, 9 stitched contracts, 0 bad stitches. Final 5 calendar
months = holdout: **OOS = 2026-02-01 -> 2026-07-01. IS = 2024-06-12 ->
2026-01-31.** Integrity verdict pending user sign-off on exceptions.
