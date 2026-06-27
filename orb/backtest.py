"""Event-driven ORB backtester.

Simulates the strategy bar-by-bar over historical OHLCV data. Designed so the
exact same `StrategyConfig` can later drive a live IBKR executor — only the data
source and order placement differ.
"""

from __future__ import annotations

import datetime as dt
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


def _opening_range(day_df: pd.DataFrame, cfg: StrategyConfig):
    """Return (or_high, or_low) from the first `or_minutes` of the session, or None."""
    open_t = dt.time.fromisoformat(cfg.session_open)
    start = day_df.index[0].replace(
        hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0
    )
    end = start + dt.timedelta(minutes=cfg.or_minutes)
    window = day_df[(day_df.index >= start) & (day_df.index < end)]
    if window.empty:
        return None
    return float(window["high"].max()), float(window["low"].min()), end


def _exit_levels(direction: str, entry: float, or_high: float, or_low: float, cfg: StrategyConfig, spec: ContractSpec):
    """Compute (stop, target, risk_points) for a fill at `entry`."""
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

    if cfg.target_type == "r_multiple":
        reward = cfg.r_multiple * risk
    elif cfg.target_type == "fixed":
        reward = cfg.target_ticks * spec.tick_size
    elif cfg.target_type == "range_multiple":
        reward = rng * cfg.range_multiple
    else:
        raise ValueError(f"unknown target_type {cfg.target_type}")

    if direction == "long":
        stop, target = entry - risk, entry + reward
    else:
        stop, target = entry + risk, entry - reward
    return _round_to_tick(stop, spec), _round_to_tick(target, spec), risk


def _simulate_day(day_df: pd.DataFrame, cfg: StrategyConfig, spec: ContractSpec) -> list[Trade]:
    sess = session_slice(day_df, cfg.session_open, cfg.session_close)
    if len(sess) < cfg.or_minutes + 2:
        return []

    orr = _opening_range(sess, cfg)
    if orr is None:
        return []
    or_high, or_low, or_end = orr

    rng_ticks = (or_high - or_low) / spec.tick_size
    if not (cfg.min_range_ticks <= rng_ticks <= cfg.max_range_ticks):
        return []

    buf = cfg.entry_buffer_ticks * spec.tick_size
    long_trigger = _round_to_tick(or_high + buf, spec)
    short_trigger = _round_to_tick(or_low - buf, spec)
    slip = cfg.slippage_ticks * spec.tick_size

    after = sess[sess.index >= or_end]
    trades: list[Trade] = []
    position: Optional[dict] = None
    n_entries = 0

    bars = list(after.itertuples())  # (Index, open, high, low, close, [volume])
    for i, bar in enumerate(bars):
        t = bar.Index
        last_of_day = i == len(bars) - 1

        # --- manage an open position ---
        if position is not None:
            d = position["direction"]
            stop, target = position["stop"], position["target"]
            hit_stop = bar.low <= stop if d == "long" else bar.high >= stop
            hit_target = bar.high >= target if d == "long" else bar.low <= target

            exit_price = exit_reason = None
            if hit_stop and hit_target:
                # Ambiguous bar: be conservative unless configured otherwise.
                if cfg.conservative_fills:
                    exit_price, exit_reason = stop, "stop"
                else:
                    exit_price, exit_reason = target, "target"
            elif hit_stop:
                exit_price, exit_reason = stop, "stop"
            elif hit_target:
                exit_price, exit_reason = target, "target"
            elif last_of_day and cfg.flatten_at_close:
                exit_price, exit_reason = float(bar.close), "close"

            if exit_price is not None:
                # adverse slippage on exit
                fill = exit_price - slip if d == "long" else exit_price + slip
                trades.append(_close(position, t, fill, exit_reason, cfg, spec))
                position = None
                if not cfg.allow_reentry:
                    n_entries = cfg.max_trades_per_day  # block further entries today
            else:
                continue  # still in a position, don't look for new entries this bar

        # --- look for a new entry ---
        if position is None and n_entries < cfg.max_trades_per_day:
            took = None
            if cfg.direction in ("both", "long") and bar.high >= long_trigger:
                fill = max(long_trigger, float(bar.open)) + slip
                took = ("long", fill)
            if took is None and cfg.direction in ("both", "short") and bar.low <= short_trigger:
                fill = min(short_trigger, float(bar.open)) - slip
                took = ("short", fill)

            if took is not None:
                d, fill = took
                stop, target, risk = _exit_levels(d, fill, or_high, or_low, cfg, spec)
                position = {
                    "direction": d,
                    "entry_time": t,
                    "entry_price": fill,
                    "stop": stop,
                    "target": target,
                    "risk": risk,
                    "day": t.date(),
                }
                n_entries += 1

    # Force-close anything still open on the final bar (e.g. no flatten flag edge case).
    if position is not None and bars:
        bar = bars[-1]
        d = position["direction"]
        fill = float(bar.close) - slip if d == "long" else float(bar.close) + slip
        trades.append(_close(position, bar.Index, fill, "close", cfg, spec))

    return trades


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


def run_backtest(df: pd.DataFrame, cfg: StrategyConfig, spec: ContractSpec = ES) -> list[Trade]:
    """Run the ORB strategy over all sessions in `df`. Returns a list of trades."""
    trades: list[Trade] = []
    for _, day_df in df.groupby(df.index.normalize()):
        trades.extend(_simulate_day(day_df, cfg, spec))
    return trades
