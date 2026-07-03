"""Contract spec and strategy configuration for the ORB bot."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSpec:
    """Spec for the instrument being traded.

    `commission_per_side` is the all-in per-contract cost (broker commission +
    exchange + NFA/clearing) per IBKR's published schedule; runners override
    StrategyConfig.commission_per_side from here when trading the instrument.
    """

    symbol: str = "ES"
    tick_size: float = 0.25            # minimum price increment, in index points
    point_value: float = 50.0          # USD per 1.00 index point per contract
    commission_per_side: float = 2.50  # all-in USD per contract per side

    @property
    def tick_value(self) -> float:
        return self.tick_size * self.point_value  # $12.50 for ES


# E-mini S&P 500 futures.
ES = ContractSpec()

# Micro E-mini: same tick grid, 1/10th the dollar value — the sane first live step.
MES = ContractSpec(symbol="MES", tick_size=0.25, point_value=5.0, commission_per_side=0.62)

# E-mini Nasdaq-100: same 0.25 tick grid, $20/point ($5/tick). All-in cost per
# IBKR schedule (2026-07): $0.85 commission + ~$1.60 exchange/NFA = ~$2.45/side.
NQ = ContractSpec(symbol="NQ", tick_size=0.25, point_value=20.0, commission_per_side=2.45)

# Micro E-mini Nasdaq-100 (reference only this round): $2/point, ~$0.62/side all-in.
MNQ = ContractSpec(symbol="MNQ", tick_size=0.25, point_value=2.0, commission_per_side=0.62)


@dataclass
class StrategyConfig:
    """All knobs for one ORB variant.

    The opening range (OR) is the high/low of the first ``or_minutes`` of the
    regular session. After the OR window closes we arm breakout stop orders just
    beyond each side of the range.
    """

    name: str = "ORB-default"

    # --- session / opening range ---
    or_minutes: int = 30               # length of the opening range window
    session_open: str = "09:30"        # RTH open, in `tz`
    session_close: str = "16:00"       # RTH close, in `tz`
    tz: str = "America/New_York"

    # --- entries ---
    direction: str = "both"            # "both" | "long" | "short"
    entry_buffer_ticks: int = 1        # trigger this many ticks beyond the OR
    max_trades_per_day: int = 1        # cap on entries per session
    allow_reentry: bool = False        # re-arm after a stop-out (up to the cap)

    # --- stops ---
    # "range":  opposite side of the OR
    # "fixed":  `stop_ticks` ticks from entry
    # "fraction": `stop_fraction` * OR-height from entry
    stop_type: str = "range"
    stop_ticks: int = 40
    stop_fraction: float = 1.0

    # --- targets ---
    # "r_multiple":    `r_multiple` * initial risk
    # "fixed":         `target_ticks` ticks from entry
    # "range_multiple":`range_multiple` * OR-height from entry
    target_type: str = "r_multiple"
    r_multiple: float = 1.0
    target_ticks: int = 40
    range_multiple: float = 1.0

    # --- entry filters (option 3) ---
    ema_trend_filter: int = 0          # 0 = off; else intraday EMA span: longs only above, shorts below
    no_entry_after: str = ""           # "" = off; e.g. "12:00" blocks new entries after this ET time

    # --- round-2 entry filters (research/ROUND2_PREREG.md §2; all off by default) ---
    vwap_filter: bool = False          # longs only above session VWAP, shorts only below (previous-bar values)
    min_or_vs_median: float = 0.0      # 0 = off; OR size must be >= this x the median of the prior `or_median_days` sessions' OR sizes
    or_median_days: int = 20           # history window for the OR-size median (needs this many prior measured ORs)
    min_atr_percentile: float = 0.0    # 0 = off; prior-day ATR(atr_days) percentile rank within the trailing `atr_lookback` ATRs must be >= this (0-100)
    atr_days: int = 14
    atr_lookback: int = 60             # needs >= 30 prior ATR observations before any day can trade

    # --- in-trade stop management (option 3) ---
    breakeven_at_r: float = 0.0        # 0 = off; move stop to entry once price reaches this R
    trailing_stop_ticks: int = 0       # 0 = off; trail stop this many ticks behind the best price

    # --- exits / costs ---
    flatten_at_close: bool = True      # close any open position at session_close
    commission_per_side: float = 2.50  # USD per contract per side
    slippage_ticks: float = 1.0        # adverse ticks applied to each fill
    contracts: int = 1
    conservative_fills: bool = True    # if a bar spans stop AND target, assume stop first

    # --- risk filter (skip days whose range is implausible) ---
    min_range_ticks: int = 4           # ignore days with a tiny OR
    max_range_ticks: int = 400         # ignore days with a blown-out OR


# A handful of variants to race against the 60% win-rate gate.
VARIANTS = [
    StrategyConfig(name="OR30-range-1R", or_minutes=30, stop_type="range", target_type="r_multiple", r_multiple=1.0),
    StrategyConfig(name="OR30-range-2R", or_minutes=30, stop_type="range", target_type="r_multiple", r_multiple=2.0),
    StrategyConfig(name="OR30-range-0.5R", or_minutes=30, stop_type="range", target_type="r_multiple", r_multiple=0.5),
    StrategyConfig(name="OR15-range-1R", or_minutes=15, stop_type="range", target_type="r_multiple", r_multiple=1.0),
    StrategyConfig(name="OR30-fixed20t-tgt20t", or_minutes=30, stop_type="fixed", stop_ticks=20, target_type="fixed", target_ticks=20),
    StrategyConfig(name="OR30-half-range-tgt-range", or_minutes=30, stop_type="fraction", stop_fraction=0.5, target_type="range_multiple", range_multiple=1.0),
    StrategyConfig(name="OR30-long-only-1R", or_minutes=30, direction="long", target_type="r_multiple", r_multiple=1.0),
    # variants exercising the option-3 features
    StrategyConfig(name="OR30-2R-ema-trend", or_minutes=30, target_type="r_multiple", r_multiple=2.0, ema_trend_filter=20),
    StrategyConfig(name="OR30-2R-trail40-be1", or_minutes=30, target_type="r_multiple", r_multiple=2.0, breakeven_at_r=1.0, trailing_stop_ticks=40),
    StrategyConfig(name="OR30-1R-amcutoff", or_minutes=30, target_type="r_multiple", r_multiple=1.0, no_entry_after="12:00"),
]


# ---------------------------------------------------------------------------
# Round-2 pre-registered hypothesis set (research/ROUND2_PREREG.md §2).
# 12 deliberate bets in three families; selection rules live in the prereg.
# All round-2 backtests run with doubled slippage — the runner enforces
# slippage_ticks=2.0; these definitions carry only the strategy shape.
# ---------------------------------------------------------------------------

ROUND2_VARIANTS = [
    # H1 — asymmetry: larger targets, trailing/breakeven, accept 35-45% win rates
    StrategyConfig(name="R2-H1a-2R-be1", r_multiple=2.0, breakeven_at_r=1.0),
    StrategyConfig(name="R2-H1b-2R-trail-be", r_multiple=2.0, breakeven_at_r=1.0,
                   trailing_stop_ticks=40),
    StrategyConfig(name="R2-H1c-3R-halfstop", stop_type="fraction", stop_fraction=0.5,
                   r_multiple=3.0, trailing_stop_ticks=60),
    StrategyConfig(name="R2-H1d-OR45-2R-be1", or_minutes=45, r_multiple=2.0,
                   breakeven_at_r=1.0),
    # H2 — regime filter: same shapes, but skip compressed/choppy days
    StrategyConfig(name="R2-H2a-be1-bigOR", r_multiple=2.0, breakeven_at_r=1.0,
                   min_or_vs_median=1.0),
    StrategyConfig(name="R2-H2b-trail-bigOR", r_multiple=2.0, breakeven_at_r=1.0,
                   trailing_stop_ticks=40, min_or_vs_median=1.0),
    StrategyConfig(name="R2-H2c-trail-atr50", r_multiple=2.0, breakeven_at_r=1.0,
                   trailing_stop_ticks=40, min_atr_percentile=50.0),
    StrategyConfig(name="R2-H2d-be1-bigOR125", r_multiple=2.0, breakeven_at_r=1.0,
                   min_or_vs_median=1.25),
    StrategyConfig(name="R2-H2e-triple", r_multiple=2.0, breakeven_at_r=1.0,
                   trailing_stop_ticks=40, min_or_vs_median=1.0, vwap_filter=True),
    # H3 — trend alignment: longs only above session VWAP, shorts only below
    StrategyConfig(name="R2-H3a-be1-vwap", r_multiple=2.0, breakeven_at_r=1.0,
                   vwap_filter=True),
    StrategyConfig(name="R2-H3b-trail-vwap", r_multiple=2.0, breakeven_at_r=1.0,
                   trailing_stop_ticks=40, vwap_filter=True),
    StrategyConfig(name="R2-H3c-OR45-vwap", or_minutes=45, r_multiple=2.0,
                   breakeven_at_r=1.0, vwap_filter=True),
]

# Max ONE pre-committed pick per family (prereg §3). H2e is the H1xH2xH3
# combination and belongs to the H2 family.
ROUND2_FAMILIES = {
    "H1": ["R2-H1a-2R-be1", "R2-H1b-2R-trail-be", "R2-H1c-3R-halfstop", "R2-H1d-OR45-2R-be1"],
    "H2": ["R2-H2a-be1-bigOR", "R2-H2b-trail-bigOR", "R2-H2c-trail-atr50",
           "R2-H2d-be1-bigOR125", "R2-H2e-triple"],
    "H3": ["R2-H3a-be1-vwap", "R2-H3b-trail-vwap", "R2-H3c-OR45-vwap"],
}
