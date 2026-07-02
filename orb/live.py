"""Live/paper ORB executor for Interactive Brokers.

Three layers, so the trading logic is provable without a brokerage account:

- `OrbSessionEngine` — pure decision core. Consumes completed 1-minute bars and
  fill events, emits `Action`s (place/cancel entries, update stop, flatten).
  It reuses the backtester's own helpers (`_exit_levels`, `_manage_stop`), so a
  config behaves identically here and in `run_backtest`.
- `ReplayRunner` — drives the engine over a historical CSV, simulating broker
  fills with the same rules as the backtester. `tests/test_strategy.py` asserts
  trade-for-trade parity between a replay and `run_backtest` on the same data.
- `IbkrRunner` — the ib_insync wiring: real-time 1-min bars, entry stop orders
  resting at the exchange in an OCA pair, each with attached stop-loss +
  take-profit children (server-side protection — no naked window), stop
  modifications for breakeven/trailing, cutoff cancellation, session flatten,
  and a daily-loss kill switch.

Safety: the paper port (7497) is the default. Live ports are refused unless
explicitly opted into (see scripts/run_live.py).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo

from .backtest import (
    Trade,
    _close,
    _exit_levels,
    _manage_stop,
    _resolve_exit,
    _round_to_tick,
    bar_minutes,
)
from .config import ES, ContractSpec, StrategyConfig


# --------------------------------------------------------------------------
# actions emitted by the engine
# --------------------------------------------------------------------------

@dataclass
class PlaceEntries:
    """Arm breakout stop orders on both sides (either side may start disabled)."""
    long_trigger: float
    short_trigger: float
    long_active: bool
    short_active: bool
    # estimated bracket children, priced from the trigger (re-priced on fill)
    long_stop: float = 0.0
    long_target: float = 0.0
    short_stop: float = 0.0
    short_target: float = 0.0


@dataclass
class SetSides:
    """Enable/disable the long/short entry orders (EMA trend filter)."""
    long_active: bool
    short_active: bool


@dataclass
class CancelEntries:
    reason: str


@dataclass
class SetBracket:
    """(Re)price the protective orders after a fill: actual stop and target."""
    stop: float
    target: float


@dataclass
class UpdateStop:
    """Tighten the protective stop (breakeven / trailing)."""
    stop: float


@dataclass
class Flatten:
    reason: str


@dataclass
class Info:
    msg: str


Action = object  # union of the dataclasses above


@dataclass
class Bar:
    """One COMPLETED bar; `time` is the bar's start, tz-aware exchange time."""
    time: dt.datetime
    open: float
    high: float
    low: float
    close: float

    @property
    def Index(self):  # so backtest helpers that read bar.Index keep working
        return self.time


# --------------------------------------------------------------------------
# decision engine
# --------------------------------------------------------------------------

class OrbSessionEngine:
    """State machine for one instrument, one config, day after day.

    Feed it completed RTH bars via `on_bar` and fills via `on_entry_fill` /
    `on_exit_fill`; execute the actions it returns. The engine never invents
    its own fills — the broker (or the replay fill simulator) is the source of
    truth for executions.

    Timing contract (mirrors the backtester exactly):
    - Entry orders are armed the moment the last opening-range bar completes,
      so they are live for the first post-OR bar.
    - The EMA side filter for bar t uses bar t-1's close vs t-1's EMA.
    - Stop management from bar t applies to later bars (the runner handles the
      close-through case where a tightened stop was crossed within bar t).
    """

    def __init__(self, cfg: StrategyConfig, spec: ContractSpec = ES, bar_min: float = 1.0):
        self.cfg = cfg
        self.spec = spec
        self.bar_min = bar_min
        self.tz = ZoneInfo(cfg.tz)
        self.open_t = dt.time.fromisoformat(cfg.session_open)
        self.close_t = dt.time.fromisoformat(cfg.session_close)
        self.cutoff = dt.time.fromisoformat(cfg.no_entry_after) if cfg.no_entry_after else None
        self.day: Optional[dt.date] = None
        self._reset_day(None)

    # --- session bookkeeping ---------------------------------------------

    def _reset_day(self, day: Optional[dt.date]) -> None:
        self.day = day
        self.state = "WAIT_OR"      # WAIT_OR | ARMED | IN_POSITION | DONE
        self.or_high = float("-inf")
        self.or_low = float("inf")
        self.or_bars = 0
        self.long_trigger = self.short_trigger = 0.0
        self.position: Optional[dict] = None
        self.n_entries = 0
        self.ema: Optional[float] = None
        self.prev_close: Optional[float] = None
        self.long_active = self.short_active = True
        self.entries_working = False

    def _or_expected(self) -> int:
        return int(round(self.cfg.or_minutes / self.bar_min))

    def _session_last_bar_time(self, t: dt.datetime) -> dt.datetime:
        close = t.replace(hour=self.close_t.hour, minute=self.close_t.minute,
                          second=0, microsecond=0)
        return close - dt.timedelta(minutes=self.bar_min)

    def entries_allowed_at(self, t: dt.datetime) -> bool:
        """Whether entry orders may be working during the bar starting at `t`."""
        if self.state != "ARMED" or not self.entries_working:
            return False
        if self.n_entries >= self.cfg.max_trades_per_day:
            return False
        et = t.astimezone(self.tz)
        if self.cutoff is not None and et.time() >= self.cutoff:
            return False
        return True

    def _sides(self) -> tuple[bool, bool]:
        """Long/short permission for the NEXT bar (prev-close vs prev-EMA)."""
        cfg = self.cfg
        if cfg.ema_trend_filter > 0:
            if self.prev_close is None or self.ema is None:
                trend_up = trend_dn = False
            else:
                trend_up = self.prev_close >= self.ema
                trend_dn = self.prev_close <= self.ema
        else:
            trend_up = trend_dn = True
        long_ok = cfg.direction in ("both", "long") and trend_up
        short_ok = cfg.direction in ("both", "short") and trend_dn
        return long_ok, short_ok

    def _update_ema(self, close: float) -> None:
        if self.cfg.ema_trend_filter > 0:
            alpha = 2.0 / (self.cfg.ema_trend_filter + 1.0)
            self.ema = close if self.ema is None else self.ema + alpha * (close - self.ema)
        self.prev_close = close

    # --- events ------------------------------------------------------------

    def on_bar(self, bar: Bar) -> list[Action]:
        """Process one completed RTH bar. Returns actions to execute."""
        t = bar.time.astimezone(self.tz)
        if not (self.open_t <= t.time() < self.close_t):
            return []  # overnight/pre-market bar — session logic is RTH-only

        actions: list[Action] = []
        if t.date() != self.day:
            self._reset_day(t.date())

        if self.state == "WAIT_OR":
            or_end = t.replace(hour=self.open_t.hour, minute=self.open_t.minute,
                               second=0, microsecond=0) + dt.timedelta(minutes=self.cfg.or_minutes)
            if self.or_bars == 0 and t.time() != self.open_t:
                # data/feed starts late: the true OR high/low is unknowable
                self.state = "DONE"
                self._update_ema(bar.close)
                return [Info(f"{t.date()}: first bar at {t.time()} != session open "
                             f"{self.open_t} — no trading today")]
            if t < or_end:
                self.or_high = max(self.or_high, bar.high)
                self.or_low = min(self.or_low, bar.low)
                self.or_bars += 1
                self._update_ema(bar.close)
                if self.or_bars == self._or_expected():
                    # OR complete the moment its last bar closes: arm entries
                    # now so they are live for the first post-OR bar.
                    actions += self._finalize_or(t)
                return actions
            # a bar at/after or_end arrived while the OR is still incomplete
            self.state = "DONE"
            self._update_ema(bar.close)
            return [Info(f"{t.date()}: opening range incomplete "
                         f"({self.or_bars}/{self._or_expected()} bars) — no trading today")]

        if self.state == "DONE":
            self._update_ema(bar.close)
            return []

        is_flatten_bar = (self.cfg.flatten_at_close
                          and t >= self._session_last_bar_time(t))

        if self.state == "IN_POSITION" and self.position is not None and not is_flatten_bar:
            # Parity with the backtester: no stop management on the ENTRY bar
            # (the fill happened during this bar; management applies from the
            # next one), and none on the flatten bar (it exits at the close).
            if self.position["entry_time"] < bar.time:
                old_stop = self.position["stop"]
                _manage_stop(self.position, bar, self.cfg, self.spec)
                if self.position["stop"] != old_stop:
                    actions.append(UpdateStop(self.position["stop"]))

        self._update_ema(bar.close)

        if self.state == "ARMED" and self.entries_working:
            long_ok, short_ok = self._sides()
            if (long_ok, short_ok) != (self.long_active, self.short_active):
                self.long_active, self.short_active = long_ok, short_ok
                actions.append(SetSides(long_ok, short_ok))

            # cutoff: pull unfilled entries once the next bar would start at/after it
            nxt = t + dt.timedelta(minutes=self.bar_min)
            if self.cutoff is not None and nxt.time() >= self.cutoff:
                self.entries_working = False
                self.state = "DONE"
                actions.append(CancelEntries("no_entry_after cutoff"))

        # flatten at the session close (the last RTH bar has completed)
        if is_flatten_bar:
            if self.state == "IN_POSITION":
                actions.append(Flatten("session close"))
            elif self.state == "ARMED" and self.entries_working:
                actions.append(CancelEntries("session close"))
            self.state = "DONE"

        return actions

    def _finalize_or(self, t: dt.datetime) -> list[Action]:
        cfg, spec = self.cfg, self.spec
        rng_ticks = (self.or_high - self.or_low) / spec.tick_size
        if not (cfg.min_range_ticks <= rng_ticks <= cfg.max_range_ticks):
            self.state = "DONE"
            return [Info(f"{t.date()}: OR range {rng_ticks:.0f} ticks outside "
                         f"[{cfg.min_range_ticks}, {cfg.max_range_ticks}] — no trading today")]

        buf = cfg.entry_buffer_ticks * spec.tick_size
        self.long_trigger = _round_to_tick(self.or_high + buf, spec)
        self.short_trigger = _round_to_tick(self.or_low - buf, spec)
        self.state = "ARMED"
        self.long_active, self.short_active = self._sides()
        self.entries_working = True

        ls, lt, _ = _exit_levels("long", self.long_trigger, self.or_high, self.or_low, cfg, spec)
        ss, st, _ = _exit_levels("short", self.short_trigger, self.or_high, self.or_low, cfg, spec)
        return [
            Info(f"{t.date()}: OR {self.or_low:.2f}-{self.or_high:.2f} "
                 f"({rng_ticks:.0f} ticks) -> triggers L {self.long_trigger:.2f} / "
                 f"S {self.short_trigger:.2f}"),
            PlaceEntries(self.long_trigger, self.short_trigger,
                         self.long_active, self.short_active,
                         long_stop=ls, long_target=lt, short_stop=ss, short_target=st),
        ]

    def on_entry_fill(self, direction: str, price: float, time: dt.datetime) -> list[Action]:
        """The broker reports an entry fill: compute the REAL bracket levels."""
        stop, target, risk = _exit_levels(direction, price, self.or_high, self.or_low,
                                          self.cfg, self.spec)
        self.position = {
            "direction": direction,
            "entry_time": time,
            "entry_price": price,
            "stop": stop,
            "target": target,
            "risk": risk,
            "day": time.astimezone(self.tz).date(),
        }
        self.n_entries += 1
        self.state = "IN_POSITION"
        self.entries_working = False
        return [Info(f"filled {direction} @ {price:.2f} (stop {stop:.2f}, "
                     f"target {target:.2f}, risk {risk:.2f} pts)"),
                SetBracket(stop, target)]

    def on_exit_fill(self, price: float, reason: str, time: dt.datetime) -> tuple[Optional[Trade], list[Action]]:
        """The broker reports the position closed. Optionally re-arm."""
        pos, self.position = self.position, None
        trade = _close(pos, time, price, reason, self.cfg, self.spec) if pos is not None else None

        actions: list[Action] = [Info(f"exit {reason} @ {price:.2f}")]
        cfg = self.cfg
        can_rearm = (
            cfg.allow_reentry
            and self.n_entries < cfg.max_trades_per_day
            and reason != "close"
            and (self.cutoff is None or time.astimezone(self.tz).time() < self.cutoff)
        )
        if can_rearm:
            self.state = "ARMED"
            self.entries_working = True
            self.long_active, self.short_active = self._sides()
            ls, lt, _ = _exit_levels("long", self.long_trigger, self.or_high, self.or_low, cfg, self.spec)
            ss, st, _ = _exit_levels("short", self.short_trigger, self.or_high, self.or_low, cfg, self.spec)
            actions.append(PlaceEntries(self.long_trigger, self.short_trigger,
                                        self.long_active, self.short_active,
                                        long_stop=ls, long_target=lt,
                                        short_stop=ss, short_target=st))
        else:
            self.state = "DONE"
        return trade, actions


# --------------------------------------------------------------------------
# replay runner: drive the engine over a CSV, simulating broker fills
# --------------------------------------------------------------------------

class ReplayRunner:
    """Replays historical bars through the engine, simulating fills with the
    backtester's own rules. Exists to PROVE the engine's decisions match
    `run_backtest` before any order touches a broker."""

    def __init__(self, cfg: StrategyConfig, spec: ContractSpec = ES, verbose: bool = False):
        self.cfg = cfg
        self.spec = spec
        self.verbose = verbose
        self.engine: Optional[OrbSessionEngine] = None
        self.trades: list[Trade] = []

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"  {msg}")

    def run(self, df) -> list[Trade]:
        from .data import session_slice

        slip = self.cfg.slippage_ticks * self.spec.tick_size
        eng = self.engine = OrbSessionEngine(self.cfg, self.spec,
                                             bar_min=bar_minutes(df.index))
        for _, day_df in df.groupby(df.index.normalize()):
            sess = session_slice(day_df, self.cfg.session_open, self.cfg.session_close)
            rows = list(sess.itertuples())
            if len(rows) < (self.cfg.or_minutes / eng.bar_min) + 2:
                continue  # mirror the backtester's too-short-session guard
            for i, row in enumerate(rows):
                bar = Bar(time=row.Index, open=float(row.open), high=float(row.high),
                          low=float(row.low), close=float(row.close))
                last = i == len(rows) - 1
                exited_this_bar = False

                # 1) simulate resting protective orders against this bar BEFORE
                # the engine sees it (a live stop fills mid-bar, not at bar end)
                if eng.state == "IN_POSITION" and eng.position is not None:
                    res = _resolve_exit(eng.position, bar, self.cfg)
                    if res is not None:
                        px, reason = res
                        d = eng.position["direction"]
                        fill = px - slip if d == "long" else px + slip
                        trade, acts = eng.on_exit_fill(fill, reason, bar.time)
                        self._record(trade, acts)
                        exited_this_bar = True

                # 2) simulate resting ENTRY stop orders against this bar. After
                # an exit, OHLC can't order prices, so (like the backtest) a
                # re-entry can't happen on the same bar as an exit.
                if not exited_this_bar and eng.entries_allowed_at(bar.time):
                    took = self._entry_fill(eng, bar)
                    if took is not None:
                        d, fill = took
                        self._acts(eng.on_entry_fill(d, fill, bar.time))
                        # entry bar: the remaining range can hit the bracket
                        res = _resolve_exit(eng.position, bar, self.cfg, entry_bar=True)
                        if res is not None:
                            px, reason = res
                            xfill = px - slip if d == "long" else px + slip
                            trade, acts = eng.on_exit_fill(xfill, reason, bar.time)
                            self._record(trade, acts)
                            exited_this_bar = True

                # 3) let the engine process the completed bar
                actions = eng.on_bar(bar)
                self._acts(actions)
                for a in actions:
                    if isinstance(a, UpdateStop) and eng.position is not None:
                        # close-through: a stop tightened during this bar whose
                        # close is beyond it was crossed — fills this bar
                        d = eng.position["direction"]
                        crossed = (bar.close <= a.stop if d == "long" else bar.close >= a.stop)
                        if crossed:
                            fill = a.stop - slip if d == "long" else a.stop + slip
                            trade, acts2 = eng.on_exit_fill(fill, "stop", bar.time)
                            self._record(trade, acts2)
                    elif isinstance(a, Flatten) and eng.position is not None:
                        d = eng.position["direction"]
                        fill = bar.close - slip if d == "long" else bar.close + slip
                        trade, acts2 = eng.on_exit_fill(fill, "close", bar.time)
                        self._record(trade, acts2)

                if last and eng.position is not None:
                    # truncated day (no flatten bar seen): force-close like the backtest
                    d = eng.position["direction"]
                    fill = bar.close - slip if d == "long" else bar.close + slip
                    trade, acts2 = eng.on_exit_fill(fill, "close", bar.time)
                    self._record(trade, acts2)
        return self.trades

    def _entry_fill(self, eng: OrbSessionEngine, bar: Bar):
        """Mirror of the backtester's entry logic for one bar."""
        o = bar.open
        long_hit = eng.long_active and bar.high >= eng.long_trigger
        short_hit = eng.short_active and bar.low <= eng.short_trigger
        took = None
        if long_hit and short_hit:
            if o >= eng.long_trigger:
                took = "long"
            elif o <= eng.short_trigger:
                took = "short"
            else:
                took = "long" if (eng.long_trigger - o) <= (o - eng.short_trigger) else "short"
        elif long_hit:
            took = "long"
        elif short_hit:
            took = "short"
        if took is None:
            return None
        slip = self.cfg.slippage_ticks * self.spec.tick_size
        if took == "long":
            return took, max(eng.long_trigger, o) + slip
        return took, min(eng.short_trigger, o) - slip

    def _record(self, trade: Optional[Trade], actions: list[Action]) -> None:
        if trade is not None:
            self.trades.append(trade)
        self._acts(actions)

    def _acts(self, actions: list[Action]) -> None:
        for a in actions:
            if isinstance(a, Info):
                self._log(a.msg)


# --------------------------------------------------------------------------
# IBKR runner
# --------------------------------------------------------------------------

PAPER_PORTS = {7497, 4002}
LIVE_PORTS = {7496, 4001}


class IbkrRunner:
    """Runs the engine against TWS / IB Gateway with ib_insync.

    Entry orders rest at the exchange as an OCA pair of stop orders, each
    carrying attached stop-loss + take-profit children priced from the trigger
    (server-side protection from the first fill). On the actual fill the
    children are re-priced to the true fill's levels. Breakeven/trailing tighten
    the child stop; the cutoff cancels unfilled entries; the session close
    flattens; a daily-loss kill switch flattens and stops the day.

    Start it BEFORE the 09:30 ET open so the engine sees the whole opening
    range (bars already printed today are warm-started through the engine, but
    fills can only happen from the moment the runner is live).
    """

    def __init__(
        self,
        cfg: StrategyConfig,
        spec: ContractSpec = ES,
        symbol: str = "ES",
        expiry: str | None = None,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 42,
        qty: int = 1,
        dry_run: bool = False,
        allow_live: bool = False,
        max_daily_loss_usd: float = 0.0,
        log_path: str = "data/live_trades.csv",
    ):
        if port in LIVE_PORTS and not allow_live:
            raise SystemExit(
                f"Port {port} is a LIVE trading port. This bot defaults to paper "
                "(7497/4002). If you really mean to trade real money, pass --live "
                "and type the confirmation."
            )
        self.cfg, self.spec = cfg, spec
        self.symbol, self.expiry = symbol, expiry
        self.host, self.port, self.client_id = host, port, client_id
        self.qty = qty
        self.dry_run = dry_run
        self.max_daily_loss = max_daily_loss_usd
        self.log_path = log_path
        self.engine = OrbSessionEngine(cfg, spec)
        self.day_pnl = 0.0
        self._pnl_date: Optional[dt.date] = None   # day_pnl is per THIS date
        self.trades: list[Trade] = []
        self._entry_trades: dict = {}   # orderId -> ib Trade (parents)
        self._stop_trade = None         # protective stop (ib Trade)
        self._target_trade = None
        self._last_bar_wall: Optional[dt.datetime] = None
        self._suppress_orders = False   # True only during warm-start replay

    # -- helpers -----------------------------------------------------------

    def _log(self, msg: str) -> None:
        now = dt.datetime.now(ZoneInfo(self.cfg.tz)).strftime("%H:%M:%S")
        print(f"[{now}] {msg}")

    def _write_trade(self, trade: Trade) -> None:
        import csv
        from pathlib import Path

        p = Path(self.log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        new = not p.exists()
        with open(p, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(trade.as_row().keys()))
            if new:
                w.writeheader()
            w.writerow(trade.as_row())

    # -- main loop -----------------------------------------------------------

    def run(self) -> None:
        import asyncio
        import sys

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        from ib_insync import IB, Future, LimitOrder, MarketOrder, StopOrder

        self._Stop, self._Limit, self._Market = StopOrder, LimitOrder, MarketOrder
        ib = self.ib = IB()
        ib.connect(self.host, self.port, clientId=self.client_id)
        mode = "PAPER" if self.port in PAPER_PORTS else ("LIVE" if self.port in LIVE_PORTS else "custom-port")
        self._log(f"connected to IBKR ({mode}, port {self.port}) "
                  f"{'[DRY-RUN: no orders will be sent]' if self.dry_run else ''}")

        from .ibkr import _qualified_es

        if self.symbol == "ES":
            contract = _qualified_es(ib, self.expiry)
        else:
            c = Future(symbol=self.symbol, exchange="CME", currency="USD",
                       lastTradeDateOrContractMonth=self.expiry or "")
            details = ib.reqContractDetails(c)
            if not details:
                raise RuntimeError(f"no contract details for {self.symbol}")
            today = dt.date.today().strftime("%Y%m%d")
            cs = sorted((d.contract for d in details),
                        key=lambda x: x.lastTradeDateOrContractMonth)
            contract = next((x for x in cs if x.lastTradeDateOrContractMonth >= today), cs[-1])
        self.contract = contract
        self._log(f"trading {contract.localSymbol or contract.symbol} "
                  f"(expiry {contract.lastTradeDateOrContractMonth})")

        # live-updating 1-min bars; each completed bar drives the engine
        bars = ib.reqHistoricalData(
            contract, endDateTime="", durationStr="1 D", barSizeSetting="1 min",
            whatToShow="TRADES", useRTH=False, formatDate=2, keepUpToDate=True,
        )
        # warm-start: feed today's already-completed bars so a mid-morning
        # start still knows the opening range. Orders are SUPPRESSED during
        # the replay — a breakout signal from an hour ago is stale, and a
        # resting stop behind the current price would fill instantly. If the
        # OR already completed before startup, today is observe-only.
        self._seen = set()
        tz = ZoneInfo(self.cfg.tz)
        today_local = dt.datetime.now(tz).date()
        self._suppress_orders = True
        for b in list(bars)[:-1]:
            self._seen.add(b.date)
            if isinstance(b.date, dt.datetime) and b.date.astimezone(tz).date() == today_local:
                for action in self.engine.on_bar(
                    Bar(time=b.date, open=b.open, high=b.high, low=b.low, close=b.close)
                ):
                    self._execute(action)
        self._suppress_orders = False
        if self.engine.state == "ARMED":
            self.engine.state = "DONE"
            self.engine.entries_working = False
            self._log("started AFTER the opening range completed — the breakout "
                      "signal is stale, so today is observe-only. Start before "
                      f"{self.cfg.session_open} ET to trade.")

        ib.orderStatusEvent += self._on_order_status
        bars.updateEvent += self._on_bars_update

        # wall-clock watchdog: bars stop printing on CME early-close days
        # (13:00 ET halt) and during feed outages, so the bar-driven flatten
        # can never fire. DAY orders expire at the close, which would leave an
        # overnight position with NO working stop — flatten on wall clock.
        import asyncio as _aio

        async def _watchdog():
            while True:
                await _aio.sleep(30)
                now = dt.datetime.now(ZoneInfo(self.cfg.tz))
                past_close = now.time() >= self.engine.close_t
                stale = (self._last_bar_wall is not None
                         and dt.datetime.now(dt.timezone.utc) - self._last_bar_wall
                         > dt.timedelta(minutes=10))
                if self.engine.state == "IN_POSITION" and (past_close or stale):
                    why = "past session close" if past_close else "no bars for 10 min (halt/outage?)"
                    self._log(f"WATCHDOG: {why} — flattening")
                    if not self.dry_run:
                        self._flatten("watchdog")
                    self.engine.state = "DONE"
                elif past_close and self._entry_trades and not self.dry_run:
                    self._log("WATCHDOG: past close — cancelling unfilled entries")
                    self._cancel_entries()

        _aio.get_event_loop().create_task(_watchdog())

        self._log("engine running — Ctrl-C to stop")
        try:
            ib.run()
        except (KeyboardInterrupt, SystemExit):
            self._log("shutting down: cancelling orders and flattening")
            if not self.dry_run:
                self._cancel_all()
                self._flatten("shutdown")
            ib.sleep(2)
        finally:
            ib.disconnect()

    # -- events --------------------------------------------------------------

    def _on_bars_update(self, bars, has_new_bar: bool) -> None:
        if not has_new_bar or len(bars) < 2:
            return
        self._last_bar_wall = dt.datetime.now(dt.timezone.utc)
        b = bars[-2]  # last COMPLETED bar
        if b.date in self._seen:
            return
        self._seen.add(b.date)
        if not isinstance(b.date, dt.datetime):
            return
        bar = Bar(time=b.date, open=b.open, high=b.high, low=b.low, close=b.close)
        for action in self.engine.on_bar(bar):
            self._execute(action)

    def _on_order_status(self, trade) -> None:
        if trade.orderStatus.status != "Filled":
            return
        oid = trade.order.orderId
        fill_px = trade.orderStatus.avgFillPrice
        now = dt.datetime.now(dt.timezone.utc)
        if oid in self._entry_trades:
            direction = "long" if trade.order.action == "BUY" else "short"
            children = getattr(trade, "_orb_children", (None, None))
            self._stop_trade, self._target_trade = children
            self._entry_trades.pop(oid, None)
            # the OTHER side's parent is cancelled by OCA; drop our handle
            for k, t in list(self._entry_trades.items()):
                self._entry_trades.pop(k, None)
            for a in self.engine.on_entry_fill(direction, fill_px, now):
                self._execute(a)
        elif self._stop_trade is not None and oid == self._stop_trade.order.orderId:
            self._book_exit(fill_px, "stop", now)
        elif self._target_trade is not None and oid == self._target_trade.order.orderId:
            self._book_exit(fill_px, "target", now)

    def _book_exit(self, price: float, reason: str, when: dt.datetime) -> None:
        trade, actions = self.engine.on_exit_fill(price, reason, when)
        self._stop_trade = self._target_trade = None
        if trade is not None:
            today = dt.datetime.now(ZoneInfo(self.cfg.tz)).date()
            if today != self._pnl_date:
                self._pnl_date, self.day_pnl = today, 0.0  # the kill switch is per-DAY
            self.trades.append(trade)
            self.day_pnl += trade.pnl_usd
            self._write_trade(trade)
            self._log(f"TRADE {trade.direction} {trade.entry_price:.2f} -> "
                      f"{trade.exit_price:.2f} [{trade.exit_reason}] "
                      f"${trade.pnl_usd:,.2f} (day ${self.day_pnl:,.2f})")
        if self.max_daily_loss > 0 and self.day_pnl <= -self.max_daily_loss:
            self._log(f"KILL SWITCH: day PnL ${self.day_pnl:,.2f} <= "
                      f"-${self.max_daily_loss:,.2f} — cancelling and stopping for the day")
            if not self.dry_run:
                self._cancel_all()
            self.engine.state = "DONE"
            self.engine.entries_working = False
            return
        for a in actions:
            self._execute(a)

    # -- order plumbing --------------------------------------------------------

    def _execute(self, action: Action) -> None:
        if isinstance(action, Info):
            self._log(action.msg)
            return
        if self._suppress_orders:
            self._log(f"warm-start (no order sent): {action}")
            return
        if self.dry_run:
            self._log(f"DRY-RUN: {action}")
            return

        if isinstance(action, PlaceEntries):
            self._place_entries(action)
        elif isinstance(action, SetSides):
            self._set_sides(action)
        elif isinstance(action, CancelEntries):
            self._log(f"cancelling entries: {action.reason}")
            self._cancel_entries()
        elif isinstance(action, SetBracket):
            self._modify_protection(stop=action.stop, target=action.target)
        elif isinstance(action, UpdateStop):
            self._modify_protection(stop=action.stop)
        elif isinstance(action, Flatten):
            self._log(f"flattening: {action.reason}")
            self._flatten(action.reason)

    def _place_entries(self, a: PlaceEntries) -> None:
        ib, qty = self.ib, self.qty
        oca = f"orb-{dt.date.today():%Y%m%d}-{self.client_id}-{self.engine.n_entries}"
        specs = []
        if a.long_active:
            specs.append(("BUY", a.long_trigger, a.long_stop, a.long_target))
        if a.short_active:
            specs.append(("SELL", a.short_trigger, a.short_stop, a.short_target))
        for action_side, trigger, stop, target in specs:
            parent = self._Stop(action_side, qty, trigger)
            parent.orderId = ib.client.getReqId()
            parent.ocaGroup, parent.ocaType = oca, 1
            parent.transmit = False
            child_side = "SELL" if action_side == "BUY" else "BUY"
            stop_o = self._Stop(child_side, qty, stop)
            stop_o.orderId = ib.client.getReqId()
            stop_o.parentId = parent.orderId
            stop_o.transmit = False
            tgt_o = self._Limit(child_side, qty, target)
            tgt_o.orderId = ib.client.getReqId()
            tgt_o.parentId = parent.orderId
            tgt_o.transmit = True
            pt = ib.placeOrder(self.contract, parent)
            st = ib.placeOrder(self.contract, stop_o)
            tt = ib.placeOrder(self.contract, tgt_o)
            pt._orb_children = (st, tt)  # adopted on fill in _on_order_status
            self._entry_trades[parent.orderId] = pt
            self._log(f"placed {action_side} stop @ {trigger:.2f} "
                      f"(bracket {stop:.2f} / {target:.2f}, OCA {oca})")

    def _set_sides(self, a: SetSides) -> None:
        # cancel the disabled side's parent (children die with it). A side that
        # becomes allowed again is re-placed on the next PlaceEntries (re-arm);
        # mid-day re-enable keeps the same triggers, so re-place it directly.
        want = {"BUY": a.long_active, "SELL": a.short_active}
        have = {t.order.action for t in self._entry_trades.values()}
        for oid, t in list(self._entry_trades.items()):
            if not want[t.order.action]:
                self.ib.cancelOrder(t.order)
                self._entry_trades.pop(oid, None)
                self._log(f"EMA filter: cancelled {t.order.action} entry")
        eng = self.engine
        missing = [side for side, on in want.items() if on and side not in have]
        if missing and eng.entries_working:
            ls, lt, _ = _exit_levels("long", eng.long_trigger, eng.or_high, eng.or_low, self.cfg, self.spec)
            ss, st, _ = _exit_levels("short", eng.short_trigger, eng.or_high, eng.or_low, self.cfg, self.spec)
            self._place_entries(PlaceEntries(
                eng.long_trigger, eng.short_trigger,
                long_active="BUY" in missing, short_active="SELL" in missing,
                long_stop=ls, long_target=lt, short_stop=ss, short_target=st,
            ))

    def _cancel_entries(self) -> None:
        for oid, t in list(self._entry_trades.items()):
            self.ib.cancelOrder(t.order)
        self._entry_trades.clear()

    def _cancel_all(self) -> None:
        self._cancel_entries()
        for t in (self._stop_trade, self._target_trade):
            if t is not None:
                self.ib.cancelOrder(t.order)
        self._stop_trade = self._target_trade = None

    def _modify_protection(self, stop: float | None = None, target: float | None = None) -> None:
        if stop is not None and self._stop_trade is not None:
            o = self._stop_trade.order
            if o.auxPrice != stop:
                o.auxPrice = stop
                self.ib.placeOrder(self.contract, o)
                self._log(f"stop -> {stop:.2f}")
        if target is not None and self._target_trade is not None:
            o = self._target_trade.order
            if o.lmtPrice != target:
                o.lmtPrice = target
                self.ib.placeOrder(self.contract, o)
                self._log(f"target -> {target:.2f}")

    def _flatten(self, reason: str) -> None:
        self._cancel_all()
        positions = [p for p in self.ib.positions()
                     if p.contract.conId == self.contract.conId and p.position != 0]
        for p in positions:
            side = "SELL" if p.position > 0 else "BUY"
            o = self._Market(side, abs(int(p.position)))
            tr = self.ib.placeOrder(self.contract, o)
            for _ in range(30):  # market order on ES fills in seconds; wait for it
                self.ib.sleep(1)
                if tr.orderStatus.status == "Filled" and tr.orderStatus.avgFillPrice:
                    break
            if tr.orderStatus.status == "Filled" and tr.orderStatus.avgFillPrice:
                self._book_exit(tr.orderStatus.avgFillPrice, "close",
                                dt.datetime.now(dt.timezone.utc))
            else:
                # never book a trade at a made-up price — it would poison the
                # trade log and could trip the kill switch on phantom loss
                self._log(f"WARNING: flatten order not confirmed within 30s "
                          f"(status {tr.orderStatus.status!r}) — trade NOT booked; "
                          "verify the position in TWS")
