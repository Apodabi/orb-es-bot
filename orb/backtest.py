"""Event-driven ORB backtester.

Simulates the strategy bar-by-bar over historical OHLCV data. Designed so the
exact same `StrategyConfig` can later drive a live IBKR executor — only the data
source and order placement differ.

Fill model (deliberately pessimistic — this feeds a go-live decision):
- Entries are stop orders: fill at max(trigger, bar open) for longs (gap-through
  entries fill at the open), plus adverse slippage.
- The ENTRY bar's remaining range is checked against the stop and target. Bar
  data can't order intrabar prices, so when the entry bar spans the stop (or
  both levels) the resolution follows `conservative_fills`: stop first.
- Stop exits on later bars fill at min(stop, open) for longs / max(stop, open)
  for shorts — a gap through the stop fills at the open, not at the stop.
- Target (limit) exits fill at the target, or at the open if the bar gaps
  beyond it in our favor.
- Stops and targets are rounded to the tick grid conservatively (stop away from
  entry = more risk, target toward entry = less reward), and `risk_points`
  reflects the ACTUAL rounded stop distance.
- The EMA trend filter reads the PREVIOUS bar's close vs the previous bar's
  EMA — the entry decision uses only information available before the bar in
  which the stop order fills. No intrabar lookahead.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, asdict
from typing import Optional

import pandas as pd

from .config import StrategyConfig, ES, ContractSpec
from .data import session_slice


@dataclass
class Trade:
    day: dt.date
    direction: str            # "long" | "short"
    entry_time: dt.datetime
    entry_price: float
    exit_time: dt.datetime
    exit_price: float
    exit_reason: str          # "target" | "stop" | "close"
    risk_points: float        # initial risk per contract, in points
    pnl_points: float         # net of slippage, per contract
    pnl_usd: float            # net of slippage AND commission, all contracts
    r_multiple: float         # realized PnL / initial risk

    def as_row(self) -> dict:
        return asdict(self)


def _round_to_tick(price: float, spec: ContractSpec) -> float:
    return round(price / spec.tick_size) * spec.tick_size


def _floor_to_tick(price: float, spec: ContractSpec) -> float:
    return math.floor(price / spec.tick_size + 1e-9) * spec.tick_size


def _ceil_to_tick(price: float, spec: ContractSpec) -> float:
    return math.ceil(price / spec.tick_size - 1e-9) * spec.tick_size


def bar_minutes(idx: pd.DatetimeIndex) -> float:
    """Modal bar interval in minutes (1.0 for 1-minute data)."""
    if len(idx) < 2:
        return 1.0
    diffs = pd.Series(idx[1:]) - pd.Series(idx[:-1])
    return float(diffs.mode().iloc[0].total_seconds() / 60.0)


def _opening_range(day_df: pd.DataFrame, cfg: StrategyConfig, bar_min: float):
    """Return (or_high, or_low, or_end) from the first `or_minutes` of the session.

    Returns None — skipping the day — if the data does not actually cover the
    opening range: the first bar must sit exactly on `session_open`, and the OR
    window must contain every expected bar. An OR computed from partial data is
    a different (wrong) range, so we refuse to trade such days rather than
    silently backtesting a fantasy breakout level.
    """
    open_t = dt.time.fromisoformat(cfg.session_open)
    first = day_df.index[0]
    if first.time() != open_t:
        return None  # data starts late — the true OR high/low is unknowable
    start = first
    end = start + dt.timedelta(minutes=cfg.or_minutes)
    window = day_df[(day_df.index >= start) & (day_df.index < end)]
    expected = int(round(cfg.or_minutes / bar_min))
    if window.empty or len(window) < expected:
        return None  # gaps inside the OR window — range would be unreliable
    return float(window["high"].max()), float(window["low"].min()), end


def _exit_levels(direction: str, entry: float, or_high: float, or_low: float, cfg: StrategyConfig, spec: ContractSpec):
    """Compute (stop, target, risk_points) for a fill at `entry`.

    Both levels are snapped to the tick grid pessimistically: the stop is
    rounded AWAY from entry (never understates risk) and the target TOWARD
    entry (never overstates reward). `risk_points` is the distance to the
    rounded stop — the risk a live order at that price would actually carry.
    """
    rng = or_high - or_low

    if cfg.stop_type == "range":
        risk = (entry - or_low) if direction == "long" else (or_high - entry)
    elif cfg.stop_type == "fixed":
        risk = cfg.stop_ticks * spec.tick_size
    elif cfg.stop_type == "fraction":
        risk = rng * cfg.stop_fraction
    else:
        raise ValueError(f"unknown stop_type {cfg.stop_type}")
    risk = max(risk, spec.tick_size)  # guard against zero-width stops

    if direction == "long":
        stop = _floor_to_tick(entry - risk, spec)
    else:
        stop = _ceil_to_tick(entry + risk, spec)
    risk = abs(entry - stop)  # actual risk of the order that would rest live

    if cfg.target_type == "r_multiple":
        reward = cfg.r_multiple * risk
    elif cfg.target_type == "fixed":
        reward = cfg.target_ticks * spec.tick_size
    elif cfg.target_type == "range_multiple":
        reward = rng * cfg.range_multiple
    else:
        raise ValueError(f"unknown target_type {cfg.target_type}")

    if direction == "long":
        target = _floor_to_tick(entry + reward, spec)
    else:
        target = _ceil_to_tick(entry - reward, spec)
    return stop, target, risk


def _manage_stop(pos: dict, bar, cfg: StrategyConfig, spec: ContractSpec) -> None:
    """Tighten the stop for an open position (breakeven + trailing). Never loosens."""
    d = pos["direction"]
    entry, risk = pos["entry_price"], pos["risk"]

    # Move to breakeven once the trade is `breakeven_at_r` R in profit.
    if cfg.breakeven_at_r > 0 and not pos.get("be_done"):
        trigger = cfg.breakeven_at_r * risk
        if d == "long" and bar.high >= entry + trigger:
            pos["stop"] = max(pos["stop"], _round_to_tick(entry, spec))
            pos["be_done"] = True
        elif d == "short" and bar.low <= entry - trigger:
            pos["stop"] = min(pos["stop"], _round_to_tick(entry, spec))
            pos["be_done"] = True

    # Trail the stop behind the best price seen.
    if cfg.trailing_stop_ticks > 0:
        dist = cfg.trailing_stop_ticks * spec.tick_size
        if d == "long":
            pos["stop"] = max(pos["stop"], _round_to_tick(bar.high - dist, spec))
        else:
            pos["stop"] = min(pos["stop"], _round_to_tick(bar.low + dist, spec))


def _resolve_exit(pos: dict, bar, cfg: StrategyConfig, entry_bar: bool = False):
    """Check one bar against the position's stop/target.

    Returns (exit_price, exit_reason) or None. When a bar touches both levels
    the order is unknowable from OHLC, so `conservative_fills` decides (stop
    first). On non-entry bars a gap through a level fills at the bar's open —
    stops fill worse, targets fill better, exactly as live orders would.
    On the entry bar the open precedes the fill, so gap logic doesn't apply.
    """
    d = pos["direction"]
    stop, target = pos["stop"], pos["target"]
    o = float(bar.open)

    hit_stop = bar.low <= stop if d == "long" else bar.high >= stop
    hit_target = bar.high >= target if d == "long" else bar.low <= target
    if not hit_stop and not hit_target:
        return None

    if hit_stop and hit_target:
        first = "stop" if cfg.conservative_fills else "target"
    elif hit_stop:
        first = "stop"
    else:
        first = "target"

    if first == "stop":
        if entry_bar:
            px = stop
        else:
            px = min(stop, o) if d == "long" else max(stop, o)
    else:
        if entry_bar:
            px = target
        else:
            px = max(target, o) if d == "long" else min(target, o)
    return px, first


def _simulate_day(day_df: pd.DataFrame, cfg: StrategyConfig, spec: ContractSpec):
    """Returns (trades, skip_reason). skip_reason is None for tradeable days."""
    sess = session_slice(day_df, cfg.session_open, cfg.session_close)
    if sess.empty:
        return [], "no_session_bars"
    bar_min = bar_minutes(sess.index)
    if len(sess) < (cfg.or_minutes / bar_min) + 2:
        return [], "short_session"

    orr = _opening_range(sess, cfg, bar_min)
    if orr is None:
        return [], "incomplete_or"
    or_high, or_low, or_end = orr

    rng_ticks = (or_high - or_low) / spec.tick_size
    if not (cfg.min_range_ticks <= rng_ticks <= cfg.max_range_ticks):
        return [], "range_filter"

    buf = cfg.entry_buffer_ticks * spec.tick_size
    long_trigger = _round_to_tick(or_high + buf, spec)
    short_trigger = _round_to_tick(or_low - buf, spec)
    slip = cfg.slippage_ticks * spec.tick_size

    after = sess[sess.index >= or_end]

    # Optional intraday EMA trend filter. The decision for a stop order working
    # during bar t can only use information available BEFORE bar t completes,
    # so the filter compares the PREVIOUS bar's close to the previous bar's EMA.
    prev_close = prev_ema = None
    if cfg.ema_trend_filter > 0:
        closes = sess["close"]
        ema = closes.ewm(span=cfg.ema_trend_filter, adjust=False).mean()
        prev_close = closes.shift(1)
        prev_ema = ema.shift(1)

    cutoff = dt.time.fromisoformat(cfg.no_entry_after) if cfg.no_entry_after else None

    trades: list[Trade] = []
    position: Optional[dict] = None
    n_entries = 0

    bars = list(after.itertuples())  # (Index, open, high, low, close, [volume])
    for i, bar in enumerate(bars):
        t = bar.Index
        last_of_day = i == len(bars) - 1

        # --- manage an open position ---
        if position is not None:
            res = _resolve_exit(position, bar, cfg)
            if res is None and last_of_day and cfg.flatten_at_close:
                res = (float(bar.close), "close")

            if res is not None:
                exit_price, exit_reason = res
                d = position["direction"]
                # adverse slippage on exit
                fill = exit_price - slip if d == "long" else exit_price + slip
                trades.append(_close(position, t, fill, exit_reason, cfg, spec))
                position = None
                if not cfg.allow_reentry:
                    n_entries = cfg.max_trades_per_day  # block further entries today
                # A bar's OHLC can't order prices around the exit, so re-arming
                # on the SAME bar would trade on pre-exit price action. Wait
                # for the next bar before looking for a new entry.
                continue
            else:
                # Still open: update stop management using THIS bar (affects later bars
                # only — no intrabar lookahead, since exits were already checked above).
                _manage_stop(position, bar, cfg, spec)
                # A stop tightened DURING this bar (breakeven/trailing) is live
                # from the moment its trigger price prints. If the bar then
                # closes beyond the new stop, price must have crossed it after
                # the move — a live stop fills that bar, not on the next one.
                d = position["direction"]
                c = float(bar.close)
                crossed = c <= position["stop"] if d == "long" else c >= position["stop"]
                if crossed:
                    fill = position["stop"] - slip if d == "long" else position["stop"] + slip
                    trades.append(_close(position, t, fill, "stop", cfg, spec))
                    position = None
                    if not cfg.allow_reentry:
                        n_entries = cfg.max_trades_per_day
                continue  # don't look for new entries while in a position

        # --- look for a new entry ---
        if cutoff is not None and t.time() >= cutoff:
            continue  # past the no-new-entries time

        if n_entries < cfg.max_trades_per_day:
            if prev_close is not None:
                pc, pe = prev_close.loc[t], prev_ema.loc[t]
                trend_up = pd.notna(pc) and pd.notna(pe) and float(pc) >= float(pe)
                trend_dn = pd.notna(pc) and pd.notna(pe) and float(pc) <= float(pe)
            else:
                trend_up = trend_dn = True
            long_ok = cfg.direction in ("both", "long") and trend_up
            short_ok = cfg.direction in ("both", "short") and trend_dn

            o = float(bar.open)
            long_hit = long_ok and bar.high >= long_trigger
            short_hit = short_ok and bar.low <= short_trigger

            took = None
            if long_hit and short_hit:
                # Bar spans both triggers: the open tells us which side was
                # reached first (or was already through at the open).
                if o >= long_trigger:
                    took = "long"
                elif o <= short_trigger:
                    took = "short"
                else:
                    took = "long" if (long_trigger - o) <= (o - short_trigger) else "short"
            elif long_hit:
                took = "long"
            elif short_hit:
                took = "short"

            if took is not None:
                if took == "long":
                    fill = max(long_trigger, o) + slip
                else:
                    fill = min(short_trigger, o) - slip
                stop, target, risk = _exit_levels(took, fill, or_high, or_low, cfg, spec)
                position = {
                    "direction": took,
                    "entry_time": t,
                    "entry_price": fill,
                    "stop": stop,
                    "target": target,
                    "risk": risk,
                    "day": t.date(),
                }
                n_entries += 1

                # The entry bar's remaining range can hit the stop or target
                # within the same bar — a live bracket would fill. Check it now.
                res = _resolve_exit(position, bar, cfg, entry_bar=True)
                if res is None and last_of_day and cfg.flatten_at_close:
                    res = (float(bar.close), "close")
                if res is not None:
                    exit_price, exit_reason = res
                    d = position["direction"]
                    fill_x = exit_price - slip if d == "long" else exit_price + slip
                    trades.append(_close(position, t, fill_x, exit_reason, cfg, spec))
                    position = None
                    if not cfg.allow_reentry:
                        n_entries = cfg.max_trades_per_day

    # Force-close anything still open on the final bar (e.g. no flatten flag edge case).
    if position is not None and bars:
        bar = bars[-1]
        d = position["direction"]
        fill = float(bar.close) - slip if d == "long" else float(bar.close) + slip
        trades.append(_close(position, bar.Index, fill, "close", cfg, spec))

    return trades, None


def _close(pos: dict, t, fill: float, reason: str, cfg: StrategyConfig, spec: ContractSpec) -> Trade:
    d = pos["direction"]
    pnl_points = (fill - pos["entry_price"]) if d == "long" else (pos["entry_price"] - fill)
    gross_usd = pnl_points * spec.point_value * cfg.contracts
    commission = 2 * cfg.commission_per_side * cfg.contracts
    pnl_usd = gross_usd - commission
    risk = pos["risk"]
    return Trade(
        day=pos["day"],
        direction=d,
        entry_time=pos["entry_time"],
        entry_price=pos["entry_price"],
        exit_time=t,
        exit_price=fill,
        exit_reason=reason,
        risk_points=risk,
        pnl_points=pnl_points,
        pnl_usd=pnl_usd,
        r_multiple=(pnl_points / risk) if risk else 0.0,
    )


def run_backtest(df: pd.DataFrame, cfg: StrategyConfig, spec: ContractSpec = ES,
                 stats: Optional[dict] = None) -> list[Trade]:
    """Run the ORB strategy over all sessions in `df`. Returns a list of trades.

    Pass a dict as `stats` to learn how many sessions were skipped and why
    (`incomplete_or`, `short_session`, `range_filter`) — days dropped by data
    quality are invisible in the trade list, and a backtest that silently ran
    on half the sessions is not the backtest you think it is.
    """
    trades: list[Trade] = []
    if stats is not None:
        stats.setdefault("sessions", 0)
        stats.setdefault("skipped", {})
    for _, day_df in df.groupby(df.index.normalize()):
        day_trades, skip = _simulate_day(day_df, cfg, spec)
        trades.extend(day_trades)
        if stats is not None:
            stats["sessions"] += 1
            if skip is not None:
                stats["skipped"][skip] = stats["skipped"].get(skip, 0) + 1
    return trades
