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

**2026-07-03 — accepted data exceptions (user sign-off) + roll-date rule.**
- (A) Roll-date sessions are retained AS-IS under the pre-registered
  expiry-minus-8-days convention: the incoming contract's first RTH day is
  thin (7 of 528 sessions, ~1.3%; 50 scattered flat zero-volume minutes;
  median book depth 12 vs 824 contracts/min). Flagged as a known
  cost-understatement risk: real slippage on those sessions likely exceeds
  the doubled 2-tick assumption. PRE-COMMITTED RULE, fixed before selection:
  if any pick reaches the holdout and passes the gate, its roll-date-session
  trades are reported separately; if excluding those trades flips the gate
  verdict from pass to fail, THE VERDICT IS FAIL.
- (B) Sixteen >1% intra-session 1-minute moves accepted as documented
  real-market events (2025-02-12 08:30 ET CPI print; 2025-04-02 16:13 tariff
  announcement; the 2025-04-06 -> 04-09 shock cluster; 2026-03-23 07:05
  pre-market move verified bar-by-bar in round 1). Retained in full —
  several sit outside the RTH window; the rest are exactly the regimes the
  hypotheses claim to handle.
No other integrity failures remain; roll stitches verified clean (0 bad-stitch).

**2026-07-03 — pre-committed INSUFFICIENT EVIDENCE rule (user-proposed,
signed off BEFORE the holdout look; justified only by IS trade frequency:
H3d 47/421 sessions -> ~12 projected OOS trades, H2c -> ~17).**
If a pick's ONLY failed criterion on the holdout is (c) >= 40 decided trades,
its verdict is INSUFFICIENT EVIDENCE, not FAIL, and does NOT trigger §f's
program-conclusion clause. The pick then freezes: parameters locked exactly
as pre-registered; each new month of NQ data accrues as extended holdout;
it is re-evaluated EXACTLY ONCE, at the first month-end where >= 40 decided
trades exist across the union of the original holdout and accrued months,
under the full unchanged gate (all seven criteria incl. benchmark alpha,
computed over that whole window) plus the roll-date rule. No interim
peeking; no parameter changes; §f's bar on new variants remains in force
during any freeze. Any other failed criterion = FAIL as normal; passing
everything = survivor as normal.

**2026-07-03 — HOLDOUT CONSUMED; verdict FAIL x2; §f program conclusion.**
One look, two picks: R3-H2c-bandwide traded ONCE in the 107-session holdout
(+$340; criteria c, d, e failed); R3-H3d-noon-band traded ZERO times (all
criteria failed vacuously). The INSUFFICIENT EVIDENCE rule's condition ("ONLY
(c) failed") was NOT met as drafted — criteria d/e also failed, albeit as
sample-size artifacts at n<=1. By the letter of the pre-registered rules both
verdicts are FAIL and §f concludes the program.

Root cause discovered post-holdout: the legacy max_range_ticks=400 cap
(100 points — a never-binding sanity limit at ES price levels) was carried
unadapted into the NQ variants, where 100 points is ~0.4% and the cap
discarded most sessions (98/107 holdout sessions even unfiltered). The
entire round — IS selection included — thus tested "ORB on unusually calm
NQ days," not ORB on NQ. Per-instrument parameterization must include
price-relative limits; this is recorded as the round's principal lesson.
Any corrected re-test belongs to a future round on fresh data — this
dataset is consumed.

Deviation disclosure: after the official look, one diagnostic run of a
non-pick (R3-H3b-noon) was made against the consumed holdout to decompose
skip reasons for this verdict. Trade count and skip reasons only; no P&L
was read or reported. The holdout was already consumed; nothing can select
on it. Recorded here for completeness.

**2026-07-03 — RULINGS 1 & 2 (user, on the record).**
RULING 1: the letter verdict on the INSUFFICIENT EVIDENCE amendment stands;
post-hoc reinterpretation forbidden. Drafting fix for FUTURE rounds: if (c)
fails and all other failed criteria are within {d, e}, verdict is
INSUFFICIENT EVIDENCE.
RULING 2: Round 3 is re-categorized FAIL -> INVALID (instrument error) under
this narrow criterion, added henceforth: "a round is INVALID only when a
mechanical harness defect, verifiable from skip logs alone without reference
to P&L, prevented registered strategy logic from executing on a supermajority
of sessions including the unfiltered diagnostic siblings." The
max_range_ticks=400 defect qualifies (98/107 holdout sessions gated even for
the unfiltered sibling). §f is NOT triggered — no valid NQ test occurred;
rounds 1-2 ES conclusions stand untouched. The 2026-02 -> 2026-07 NQ holdout
remains PERMANENTLY CONSUMED regardless and is excluded from the corrected
round entirely (not IS, not holdout). The post-look diagnostic (counts and
skip reasons only, no P&L) is ratified as legitimate. STANDING CHECKLIST
RULE: no future holdout look until skip reasons are decomposed and
trade-count denominators are explained.

**2026-07-03 — ROUND 3-CORRECTED protocol (locked before re-selection).**
- Harness fix: session-gating thresholds become percentage-of-price
  (min_range_pct=0.02%, max_range_pct=2.0% of the OR midpoint — the exact
  ES-level intent of the old 4/400-tick caps), unit-tested on both ES- and
  NQ-level prices. Audit conclusion: registered variant parameters
  (trailing_stop_ticks, confirm_beyond_ticks, entry_buffer_ticks, slippage
  in ticks per §a) are LOCKED variant identity and are retained as written;
  their price-level dependence is a documented lesson for future design.
- IS re-selection: 2024-06 -> 2026-01, locked round-3 variant set only,
  doubled slippage, $2.45/side, <=1 pick per family, <=3 total.
- Picks freeze. New holdout: live NQ data accruing from 2026-07-01 forward.
  One re-evaluation per pick at the first month-end where it holds >= 40
  decided trades, under the full unchanged gate incl. (g) and the roll-date
  rule. Monthly trade-count checks permitted with P&L output suppressed.
  If 9 months elapse without 40 decided trades: INSUFFICIENT EVIDENCE.
  No interim parameter changes, no new variants, no peeking.
