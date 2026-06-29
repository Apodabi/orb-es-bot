"""
ibkr_data.py  --  IBKR Client Portal Web API fetcher for the orb-es-bot pipeline.

A drop-in alternative to scripts/fetch_data.py that pulls real ES bars via the
Web API (Client Portal Gateway) instead of TWS/ib_insync, and writes the SAME
canonical CSV the rest of the repo expects:

    columns: timestamp, open, high, low, close, volume   (timestamp in UTC)

so it feeds straight into the existing tooling:

    python ibkr_data.py --month 202609 --days 60 --out data/es_1min.csv
    python scripts/compare.py  --data data/es_1min.csv --source-tz UTC
    python scripts/validate.py --data data/es_1min.csv --source-tz UTC
    python scripts/sweep.py    --data data/es_1min.csv --source-tz UTC

Prereqs:
  1. Client Portal Gateway running + authenticated (2FA) at https://localhost:5000.
     Keep it alive in a live loop with tickle(); for a one-shot fetch it's not needed.
  2. CME real-time market-data subscription on the IB account, or ES bars come back
     empty/delayed.

WHY THIS IS THE SECONDARY ROUTE: the Web API /iserver/marketdata/history endpoint is
rate-limited (5 concurrent, 10 req/s), capped per request, and not designed for bulk
historical pulls. For long 1-min histories the repo's existing TWS path
(scripts/fetch_data.py -> orb/ibkr.py, ib_insync reqHistoricalData) is more reliable.
Use this when you don't want a TWS process running, or for live/snapshot data later.

Endpoints (confirmed against IBKR Campus Web API docs):
  GET  /iserver/auth/status         POST /iserver/auth/ssodh/init   POST /tickle
  GET  /iserver/accounts            (required once before any market data)
  GET  /trsrv/futures?symbols=ES    (resolve ES conids by expiration)
  GET  /iserver/marketdata/history  (period / bar / outsideRth / startTime)
  GET  /iserver/marketdata/snapshot (live top-of-book; for the future live executor)
"""

from __future__ import annotations

import time
import datetime as dt
from typing import Optional

import requests
import pandas as pd
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://localhost:5000/v1/api"   # Client Portal Gateway default


class IBKRError(RuntimeError):
    pass


class IBKRWebAPI:
    def __init__(self, base_url: str = BASE_URL, timeout: int = 15):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.s = requests.Session()
        self.s.verify = False  # gateway uses a self-signed cert

    # --- low level ---------------------------------------------------------
    def _get(self, path: str, **params):
        r = self.s.get(f"{self.base}{path}", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json=None):
        r = self.s.post(f"{self.base}{path}", json=json or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if r.text else {}

    # --- session -----------------------------------------------------------
    def ensure_session(self) -> None:
        """Confirm an authenticated brokerage session; init it if needed."""
        status = self._get("/iserver/auth/status")
        if not status.get("authenticated"):
            self._post("/iserver/auth/ssodh/init", {"publish": True, "compete": True})
            time.sleep(2)
            status = self._get("/iserver/auth/status")
        if not status.get("authenticated"):
            raise IBKRError(
                "Brokerage session not authenticated. Start the Client Portal Gateway "
                "and log in (2FA) at https://localhost:5000 before fetching."
            )
        self._get("/iserver/accounts")  # market data requires accounts loaded once

    def tickle(self) -> None:
        """Call ~every 60s in a live loop to avoid the ~5min session timeout."""
        self._post("/tickle")

    # --- contract resolution ----------------------------------------------
    def resolve_es_conid(self, month: Optional[str] = None) -> dict:
        """ES futures record. month='YYYYMM' (e.g. '202609'); None -> front month."""
        data = self._get("/trsrv/futures", symbols="ES")
        contracts = data.get("ES", [])
        if not contracts:
            raise IBKRError("No ES futures returned. Check the CME data subscription.")
        exp = lambda c: str(c.get("expirationDate"))
        contracts.sort(key=exp)
        if month:
            for c in contracts:
                if exp(c).startswith(month):
                    return c
            raise IBKRError(f"No ES contract for {month}. Available: "
                            f"{[exp(c)[:6] for c in contracts]}")
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
        for c in contracts:
            if exp(c) >= today:
                return c
        return contracts[-1]

    # --- historical bars ---------------------------------------------------
    def get_history(self, conid: int, period: str = "1d", bar: str = "1min",
                    outside_rth: bool = False, start_time: Optional[dt.datetime] = None,
                    retries: int = 5) -> pd.DataFrame:
        """
        OHLCV bars -> UTC-indexed DataFrame.
        bar:    1min,2min,3min,5min,15min,30min,1h,1d
        period: window extends BACK from start_time (1d,2d,1w,1m,...)
        start_time: most-recent edge of the window (UTC); defaults to 'now'.
        """
        params = {"conid": conid, "period": period, "bar": bar,
                  "outsideRth": str(outside_rth).lower()}
        if start_time is not None:
            params["startTime"] = start_time.strftime("%Y%m%d-%H:%M:%S")
        payload = {}
        for attempt in range(retries):
            try:
                payload = self._get("/iserver/marketdata/history", **params)
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else None
                if code in (429, 503) and attempt < retries - 1:
                    # Pacing / transient unavailability: exponential backoff.
                    time.sleep(2 ** attempt)
                    continue
                raise
            if payload.get("data"):
                break
            time.sleep(1)  # known empty-first-response quirk
        # Keep only real bars. The API can return a 1-row placeholder
        # (startTime 19700101, no "t") for windows with no data — e.g. the
        # current day before the session — which must not be parsed as a bar.
        rows = [r for r in payload.get("data", [])
                if isinstance(r, dict) and "t" in r and "o" in r]
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = (pd.DataFrame(rows)
                .rename(columns={"o": "open", "h": "high", "l": "low",
                                 "c": "close", "v": "volume"}))
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        return (df.set_index("timestamp").sort_index()
                  [["open", "high", "low", "close", "volume"]])

    def get_history_days(self, conid: int, days: int, bar: str = "1min",
                         outside_rth: bool = False) -> pd.DataFrame:
        """
        Walk back `days` calendar days, one request per day (gentler on the Web API
        than one huge 1-min window). 22:00 UTC is always after the 16:00 ET RTH close
        in both DST regimes, so each 1d window captures that ET session. Empty days
        (weekends/holidays) are skipped.
        """
        frames = []
        today = dt.datetime.now(dt.timezone.utc)
        for i in range(days):
            edge = (today - dt.timedelta(days=i)).replace(
                hour=22, minute=0, second=0, microsecond=0)
            df = self.get_history(conid, period="1d", bar=bar,
                                  outside_rth=outside_rth, start_time=edge)
            if not df.empty:
                frames.append(df)
            time.sleep(0.75)  # stay under the historical-data pacing limit
        if not frames:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        out = pd.concat(frames)
        return out[~out.index.duplicated(keep="last")].sort_index()

    # --- live snapshot (for the future live executor) ----------------------
    def get_snapshot(self, conid: int, fields=("31", "84", "86")) -> dict:
        """31=last, 84=bid, 86=ask. First call may be sparse; poll again."""
        data = self._get("/iserver/marketdata/snapshot",
                          conids=str(conid), fields=",".join(fields))
        return data[0] if isinstance(data, list) and data else {}


def fetch_es_bars_webapi(month: Optional[str] = None, days: int = 60,
                         bar: str = "1min", outside_rth: bool = False,
                         base_url: str = BASE_URL) -> pd.DataFrame:
    """Return a frame with a `timestamp` column (UTC) + OHLCV — same shape as
    orb.ibkr.fetch_es_bars, so it's interchangeable in the pipeline."""
    ib = IBKRWebAPI(base_url)
    ib.ensure_session()
    es = ib.resolve_es_conid(month)
    print(f"ES contract: conid={es['conid']} exp={es.get('expirationDate')}")
    df = ib.get_history_days(int(es["conid"]), days=days, bar=bar,
                             outside_rth=outside_rth)
    # canonical CSV: UTC, tz-naive timestamp column (pair with --source-tz UTC)
    df = df.copy()
    if df.empty:
        # No bars came back (e.g. no CME real-time subscription). Return the
        # canonical empty shape instead of crashing on a RangeIndex.tz_convert.
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    df.index = df.index.tz_convert("UTC").tz_localize(None)
    return df.reset_index().rename(columns={"index": "timestamp"})


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Fetch ES bars via the IBKR Web API.")
    ap.add_argument("--month", default=None, help="contract month YYYYMM, e.g. 202609 (default: front month)")
    ap.add_argument("--days", type=int, default=60, help="calendar days of history to pull")
    ap.add_argument("--bar", default="1min", help="1min,2min,5min,15min,30min,1h,1d")
    ap.add_argument("--outside-rth", action="store_true", help="include overnight session")
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--out", default="data/es_1min.csv")
    args = ap.parse_args()

    frame = fetch_es_bars_webapi(month=args.month, days=args.days, bar=args.bar,
                                 outside_rth=args.outside_rth, base_url=args.base_url)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False)
    sessions = pd.to_datetime(frame["timestamp"]).dt.normalize().nunique() if len(frame) else 0
    print(f"saved {len(frame):,} bars across {sessions} sessions -> {args.out}")
    print("Timestamps are UTC; backtest with --source-tz UTC.")
