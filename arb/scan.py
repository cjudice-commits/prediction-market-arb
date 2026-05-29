"""Load seed data, fan out the live API calls, evaluate every active pair."""
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from . import kalshi, poly
from .calc import evaluate

_DATA = os.path.join(os.path.dirname(__file__), "..", "data")
_LOCK = threading.Lock()
_CACHE = {"ts": 0.0, "payload": None}
_HCACHE = {"ts": 0.0, "payload": None}
_HLOCK = threading.Lock()
_PCACHE = {"ts": 0.0, "payload": None}
_PLOCK = threading.Lock()
_POS_TTL = 12
_DCACHE = {"ts": 0.0, "payload": None}
_DLOCK = threading.Lock()
_DAILY_TTL = 6
_CACHE_TTL = 4  # seconds; just enough to dedupe rapid refreshes


def _path(name):
    return os.path.abspath(os.path.join(_DATA, name))


def load_settings():
    with open(_path("settings.json")) as f:
        return json.load(f)


def load_pairs():
    with open(_path("pairs.json")) as f:
        return json.load(f)


def run_scan(force=False):
    """Returns {rows, summary, generated_at, settings}. Cached for _CACHE_TTL."""
    with _LOCK:
        now = time.time()
        if (not force) and _CACHE["payload"] and (now - _CACHE["ts"] < _CACHE_TTL):
            return _CACHE["payload"]

        settings = load_settings()
        pairs = load_pairs()
        active = [p for p in pairs if p.get("active")]

        tickers = [p["kalshi_ticker"] for p in active if p.get("kalshi_ticker")]
        slugs = [p["poly_slug"] for p in active if p.get("poly_slug")]

        t0 = time.time()
        # Hit both venues at the same time instead of one after the other.
        with ThreadPoolExecutor(max_workers=2) as ex:
            fk = ex.submit(kalshi.fetch_quotes, tickers)
            fp = ex.submit(poly.fetch_quotes, slugs)
            kquotes = fk.result()
            pquotes = fp.result()

        def _eval():
            return [
                evaluate(p, kquotes.get(p["kalshi_ticker"]),
                         pquotes.get(p.get("poly_slug")), settings)
                for p in active
            ]

        rows = _eval()

        # Phase 2: for basis-favorable, near/above-breakeven rows, replace the
        # open-interest proxy with true Kalshi top-of-book size, then re-eval.
        cand_tickers = [
            r["kalshi_ticker"] for r in rows
            if r.get("basis_favorable")
            and r.get("worst_pnl") is not None
            and r.get("combined_cost")
            and r["worst_pnl"] > -0.05 * r["combined_cost"]
        ]
        if cand_tickers:
            for tk, sz in kalshi.fetch_sizes(cand_tickers).items():
                if sz and kquotes.get(tk):
                    kquotes[tk]["yes_ask_size"] = sz["yes_ask_size"]
                    kquotes[tk]["no_ask_size"] = sz["no_ask_size"]
            rows = _eval()

        fetch_ms = int((time.time() - t0) * 1000)

        summary = {"total": len(rows), "fetch_ms": fetch_ms}
        for st in ("ARB", "NO ARB", "BAD BASIS", "LOW SIZE",
                   "NO DATA", "NO POLY", "NO KALSHI", "NO PAIR"):
            summary[st] = sum(1 for r in rows if r["status"] == st)

        payload = {
            "rows": rows,
            "summary": summary,
            "settings": settings,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime()),
        }
        _CACHE["ts"] = now
        _CACHE["payload"] = payload
        return payload


def run_hourly(force=False):
    """Kalshi hourly ladder vs Polymarket Up/Down. Cached for _CACHE_TTL."""
    from . import hourly
    with _HLOCK:
        now = time.time()
        if (not force) and _HCACHE["payload"] and \
                (now - _HCACHE["ts"] < _CACHE_TTL):
            return _HCACHE["payload"]

        settings = load_settings()
        t0 = time.time()
        rows = hourly.run(settings)
        fetch_ms = int((time.time() - t0) * 1000)

        summary = {"total": len(rows), "fetch_ms": fetch_ms}
        for st in ("ARB", "NO ARB", "BAD BASIS", "LOW SIZE",
                   "NO DATA", "NO KALSHI"):
            summary[st] = sum(1 for r in rows if r["status"] == st)
        live = [r for r in rows if r.get("divergence") is not None]
        summary["max_divergence"] = (
            max(abs(r["divergence"]) for r in live) if live else None)

        payload = {
            "rows": rows,
            "summary": summary,
            "settings": settings,
            "mode": "hourly",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime()),
        }
        _HCACHE["ts"] = now
        _HCACHE["payload"] = payload
        return payload


def run_positions(force=False):
    """Live Polymarket + Kalshi positions. Cached for _POS_TTL."""
    from . import positions
    with _PLOCK:
        now = time.time()
        if (not force) and _PCACHE["payload"] and \
                (now - _PCACHE["ts"] < _POS_TTL):
            return _PCACHE["payload"]
        payload = positions.run_positions()
        _PCACHE["ts"] = now
        _PCACHE["payload"] = payload
        return payload


def run_daily(force=False):
    """IBKR × Kalshi daily-BTC cross-reference. Cached for _DAILY_TTL."""
    from . import daily
    with _DLOCK:
        now = time.time()
        if (not force) and _DCACHE["payload"] and \
                (now - _DCACHE["ts"] < _DAILY_TTL):
            return _DCACHE["payload"]
        payload = daily.run(load_settings())
        _DCACHE["ts"] = now
        _DCACHE["payload"] = payload
        return payload
