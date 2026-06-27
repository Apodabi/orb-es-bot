"""Contract spec and strategy configuration for the ORB bot."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSpec:
    """Spec for the instrument being traded."""

    symbol: str = "ES"
    tick_size: float = 0.25            # minimum price increment, in index points
    point_value: float = 50.0          # USD per 1.00 index point per contract

    @property
    def tick_value(self) -> float:
        return self.tick_size * self.point_value  # $12.50 for ES


# E-mini S&P 500 futures.
ES = ContractSpec()


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
