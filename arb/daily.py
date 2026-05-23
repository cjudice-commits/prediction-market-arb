"""IBKR ForecastEx daily BTC × Kalshi KXBTCD hourly cross-reference.

Pairing model: each IBKR ForecastEx daily BTC contract settles at a specific
clock-time on a specific date. Kalshi's hourly KXBTCD ladder has a contract
closing at exactly that same instant. Same strike + same close = same product
across two venues, so the existing same-strike arb math applies.

The user supplies IBKR contract conids in data/ibkr_contracts.json (one-time;
they grab them from the IBKR UI after the Client Portal Gateway is auth'd).
Each entry: {conid, strike (dollars), close_iso (UTC), label}. We then:

  1. Snapshot prices for all configured IBKR conids via the local Gateway.
  2. For each entry, derive the Kalshi event ticker from close_iso (ET hour),
     fetch the event's strike ladder, pick the market with the nearest strike.
  3. Run the standard worst-case arb math (arb.calc.evaluate) treating IBKR
     as the "second venue" (drop into the pq slot).

ForecastEx contract semantics (assumed; verify on first Gateway test):
  - One conid per "above $X" daily contract. Buying = long YES.
  - yes_ask = snapshot.ask (price to BUY YES = go long).
  - no_ask  = 1 - snapshot.bid (price to "buy NO" = sell/short; you'd
              receive `bid` selling so net cost to take the NO side is 1-bid).
  - If this representation turns out wrong, only this file changes.
"""
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .net import get_json, FetchError
from . import ibkr
from .calc import evaluate

ROOT = Path(__file__).resolve().parent.parent
IBKR_CONTRACTS = ROOT / "data" / "ibkr_contracts.json"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                       # no tzdata -> EDT fallback (Mar–Nov)
    _ET = timezone(timedelta(hours=-4))

_MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
        "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _event_ticker_for(close_iso, series="KXBTCD"):
    """'2026-05-23T21:00:00Z' -> 'KXBTCD-26MAY2317'  (ET-hour encoding)."""
    if not close_iso:
        return None
    try:
        dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    et = dt.astimezone(_ET)
    return "%s-%s%s%02d%02d" % (
        series, str(et.year)[-2:], _MON[et.month - 1], et.day, et.hour)


def _kalshi_ladder(event_ticker):
    try:
        ev = get_json("%s/events/%s?with_nested_markets=true" %
                      (KALSHI, event_ticker))
    except FetchError:
        return []
    return (ev.get("event") or {}).get("markets") or []


def _pick_kalshi(ladder, target_strike):
    """Pick the 'or above' market whose numeric strike is nearest target."""
    best = None
    for m in ladder:
        sub = (m.get("yes_sub_title") or "").lower()
        if "or above" not in sub:
            continue
        t = (m.get("ticker") or "").rsplit("-T", 1)
        if len(t) != 2:
            continue
        try:
            ks = float(t[1])
        except ValueError:
            continue
        d = abs(ks - target_strike)
        if best is None or d < best[0]:
            best = (d, ks, m)
    return best                          # (dist, strike, market) | None


def _kq_from_market(m):
    """Adapt a Kalshi market dict to the kq shape calc.evaluate expects."""
    def f(v):
        try:
            x = float(v)
            return x if x > 0 else None
        except (TypeError, ValueError):
            return None
    return {
        "ticker": m.get("ticker"),
        "yes_ask": f(m.get("yes_ask_dollars")),
        "no_ask": f(m.get("no_ask_dollars")),
        "yes_bid": f(m.get("yes_bid_dollars")),
        "no_bid": f(m.get("no_bid_dollars")),
        "yes_ask_size": None, "no_ask_size": None,
        "open_interest": f(m.get("open_interest_fp")) or 0.0,
        "status": m.get("status"),
        "expiry": (m.get("close_time") or "")[:10],
        "title": m.get("title"),
        "yes_label": m.get("yes_sub_title"),
        "no_label": m.get("no_sub_title"),
        "rules": (m.get("rules_primary") or "").strip()[:360],
    }


def _pq_from_ibkr(snap, label):
    """Adapt an IBKR snapshot to the pq shape calc.evaluate expects.
    yes_ask = ask (long YES); no_ask = 1 - bid (short YES ~= long NO)."""
    bid = snap.get("bid"); ask = snap.get("ask")
    yes_ask = ask if (ask and 0 < ask <= 1) else None
    no_ask = (1.0 - bid) if (bid and 0 < bid < 1) else None
    return {
        "slug": None,
        "question": label,
        "description": None,
        "image": None, "icon": None,
        "yes_ask": yes_ask, "no_ask": no_ask,
        "yes_ask_size": snap.get("ask_size"),
        "no_ask_size": snap.get("bid_size"),
        "volume": None,
        "end_date": None,
        "closed": False,
    }


def _load_cfg():
    """Returns (cfg_dict, error_str_or_None)."""
    if not IBKR_CONTRACTS.exists():
        return None, "no_contracts_file"
    try:
        return json.loads(IBKR_CONTRACTS.read_text()), None
    except ValueError as e:
        return None, "bad_json: %s" % e
    except OSError as e:
        return None, "read_error: %s" % e


def run(settings):
    cfg, err = _load_cfg()
    if err or not cfg or not cfg.get("contracts"):
        return {"configured": False,
                "reason": err or "no_contracts_file",
                "rows": [], "gateway": {"connected": False}}

    auth = ibkr.auth_status()
    if not auth.get("connected"):
        return {"configured": False, "reason": "gateway_not_running",
                "rows": [], "gateway": auth}
    if not auth.get("authenticated"):
        return {"configured": True, "error": "Gateway up but session not "
                "authenticated — re-login via the IBKR browser SSO page.",
                "rows": [], "gateway": auth}
    ibkr.tickle()                                # extend session

    conids = [c.get("conid") for c in cfg["contracts"] if c.get("conid")]
    # IB market data subscription warms up on first call; retry once.
    snaps = ibkr.snapshot(conids)
    if any(s.get("ask") is None and s.get("bid") is None for s in snaps.values()):
        time.sleep(1.0)
        snaps = ibkr.snapshot(conids) or snaps

    rows = []
    for entry in cfg["contracts"]:
        cid = str(entry.get("conid") or "")
        strike = entry.get("strike")
        close_iso = entry.get("close_iso")
        label = entry.get("label") or ("IBKR conid %s" % cid)
        if not (cid and strike and close_iso):
            continue
        snap = snaps.get(cid) or {}

        event_ticker = _event_ticker_for(close_iso, series="KXBTCD")
        ladder = _kalshi_ladder(event_ticker) if event_ticker else []
        pick = _pick_kalshi(ladder, float(strike))
        if not pick:
            rows.append({
                "asset": entry.get("asset", "BTC"),
                "ibkr_conid": cid, "ibkr_label": label,
                "ibkr_strike": strike, "ibkr_close_iso": close_iso,
                "kalshi_event": event_ticker,
                "ibkr_bid": snap.get("bid"), "ibkr_ask": snap.get("ask"),
                "status": "NO KALSHI",
                "note": "no matching Kalshi KXBTCD event/strike",
            })
            continue

        dist, kstrike, kmkt = pick
        kq = _kq_from_market(kmkt)
        pq = _pq_from_ibkr(snap, label)
        pair = {
            "asset": entry.get("asset", "BTC"),
            "kalshi_ticker": kmkt.get("ticker"),
            "kalshi_strike": kstrike,
            "poly_slug": cid,           # repurposed slot — pair id
            "poly_strike": float(strike),
            "active": True,
        }
        row = evaluate(pair, kq, pq, settings)
        # Rebadge poly→IBKR for UI consumption.
        row["ibkr_conid"] = cid
        row["ibkr_label"] = label
        row["ibkr_strike"] = float(strike)
        row["ibkr_ask"] = snap.get("ask")
        row["ibkr_bid"] = snap.get("bid")
        row["kalshi_close_iso"] = (kmkt.get("close_time") or close_iso)
        row["kalshi_event"] = event_ticker
        rows.append(row)

    summary = {"total": len(rows), "ARB": sum(1 for r in rows if r.get("status") == "ARB"),
               "NO ARB": sum(1 for r in rows if r.get("status") == "NO ARB"),
               "BAD BASIS": sum(1 for r in rows if r.get("status") == "BAD BASIS"),
               "LOW SIZE": sum(1 for r in rows if r.get("status") == "LOW SIZE"),
               "NO DATA": sum(1 for r in rows if r.get("status") == "NO DATA"),
               "NO KALSHI": sum(1 for r in rows if r.get("status") == "NO KALSHI")}
    return {"configured": True, "rows": rows, "summary": summary,
            "gateway": auth,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime())}
