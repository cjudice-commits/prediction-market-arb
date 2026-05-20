"""Hourly scanner: Kalshi hourly strike ladder vs Polymarket "Up or Down".

Pairing model
-------------
Polymarket "{ASSET} Up or Down - {date} {H}{am/pm} ET" resolves Up if the
Binance 1h candle [H:00 -> H+1:00] closes >= it opens. So its *implied strike*
is the Binance candle OPEN at H:00, and it settles at H+1:00 ET.

Kalshi `KX{SYM}D-{YYMMMDD}{HH}-T{strike}` resolves Yes if the asset is >=
strike at HH:00 ET (CF Benchmarks). We pick the Kalshi market that settles at
the Polymarket window's CLOSE hour, with the strike nearest the Binance open.
Then Kalshi-Yes ~= Polymarket-Up, and the existing worst-case math handles the
(discrete strike) vs (exact open) gap as basis.

This is NOT a locked arb: the two venues settle on different price feeds
(CF Benchmarks vs Binance). `refs` surfaces that divergence live per asset.
"""
import time
from datetime import datetime, timezone, timedelta

from .net import get_json, FetchError
from . import poly, refs
from .calc import evaluate

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# asset -> (kalshi hourly series, polymarket slug name)
MARKETS = {
    "BTC": ("KXBTCD", "bitcoin"),
    "ETH": ("KXETHD", "ethereum"),
    "SOL": ("KXSOLD", "solana"),
    "XRP": ("KXXRPD", "xrp"),
    "DOGE": ("KXDOGED", "dogecoin"),
    "BNB": ("KXBNBD", "bnb"),
}
_MON = ["january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december"]

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                       # no tzdata -> EDT (valid Mar-Nov)
    _ET = timezone(timedelta(hours=-4))


def _et(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(_ET)


def _poly_slug(name, dt_et):
    h = dt_et.hour
    ampm = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return "%s-up-or-down-%s-%d-%d-%d%s-et" % (
        name, _MON[dt_et.month - 1], dt_et.day, dt_et.year, h12, ampm)


def _strike_from_ticker(t):
    """KX..-T89799.99 -> 89799.99 ; range/below buckets -> None."""
    i = t.rfind("-T")
    if i == -1:
        return None
    try:
        return float(t[i + 2:])
    except ValueError:
        return None


def _kalshi_ladder(series):
    d = get_json("%s/markets?series_ticker=%s&status=open&limit=1000"
                 % (KALSHI, series))
    return d.get("markets", [])


def _pick(markets, close_iso, target):
    """Among markets settling at close_iso, the '... or above' market whose
    strike is nearest `target`."""
    best = None
    for m in markets:
        if (m.get("close_time") or "")[:16] != close_iso[:16]:
            continue
        if "or above" not in (m.get("yes_sub_title") or "").lower():
            continue
        k = _strike_from_ticker(m.get("ticker") or "")
        if k is None:
            continue
        d = abs(k - target)
        if best is None or d < best[0]:
            best = (d, k, m)
    return best  # (dist, strike, market) | None


def _kq(m, strike):
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
        "expiry": None,                 # intraday; handled via minutes field
        "title": m.get("title"),
        "yes_label": m.get("yes_sub_title"),
        "no_label": m.get("no_sub_title"),
        "rules": (m.get("rules_primary") or "").strip()[:360],
    }


def run(settings, assets=None):
    assets = assets or list(MARKETS)
    rf = refs.fetch_refs(assets)

    # Build the current-hour Polymarket slug per asset, fetch them batched.
    slug_of, want = {}, []
    for a in assets:
        r = rf.get(a)
        if not r:
            continue
        start_et = _et(r["hour_open_ms"])
        slug = _poly_slug(MARKETS[a][1], start_et)
        slug_of[a] = (slug, r, start_et)
        want.append(slug)
    pq_all = poly.fetch_quotes(want) if want else {}

    now_ms = time.time() * 1000
    rows = []
    for a in assets:
        meta = slug_of.get(a)
        r = rf.get(a)
        base = {
            "asset": a,
            "implied_strike": r["hour_open"] if r else None,
            "binance_spot": r["binance_spot"] if r else None,
            "cf_spot": r["cf_spot"] if r else None,
            "divergence": r["divergence"] if r else None,
        }
        if not meta:
            rows.append({**_empty_row(a), **base, "status": "NO DATA"})
            continue
        slug, ref, start_et = meta
        close_dt = start_et + timedelta(hours=1)
        close_iso = (start_et.astimezone(timezone.utc) +
                     timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")

        series = MARKETS[a][0]
        try:
            ladder = _kalshi_ladder(series)
        except FetchError:
            ladder = []
        pick = _pick(ladder, close_iso, ref["hour_open"])
        pq = pq_all.get(slug)

        if not pick or not pq:
            rows.append({
                **_empty_row(a), **base,
                "poly_slug": slug, "poly_question": (pq or {}).get("question"),
                "window_start": start_et.strftime("%H:%M ET"),
                "window_close": close_dt.strftime("%H:%M ET"),
                "minutes_to_resolve": max(0, round(
                    (ref["hour_open_ms"] + 3600_000 - now_ms) / 60000)),
                "status": "NO DATA" if not pq else "NO KALSHI",
            })
            continue

        _, kstrike, kmkt = pick
        pair = {
            "asset": a, "kalshi_ticker": kmkt.get("ticker"),
            "kalshi_strike": kstrike, "poly_slug": slug,
            "poly_strike": ref["hour_open"], "active": True,
        }
        row = evaluate(pair, _kq(kmkt, kstrike), pq, settings)
        row.update(base)
        row["window_start"] = start_et.strftime("%H:%M ET")
        row["window_close"] = close_dt.strftime("%H:%M ET")
        row["minutes_to_resolve"] = max(0, round(
            (ref["hour_open_ms"] + 3600_000 - now_ms) / 60000))
        rows.append(row)
    return rows


def _empty_row(a):
    return {
        "asset": a, "kalshi_ticker": None, "kalshi_strike": None,
        "poly_slug": None, "poly_strike": None, "direction": "Above",
        "basis_pct": None, "basis_favorable": None, "best_side": None,
        "kalshi_price": None, "kalshi_size": None, "poly_price": None,
        "poly_size": None, "combined_cost": None, "kalshi_fee": None,
        "poly_fee": None, "total_fee": None, "worst_pnl": None,
        "best_pnl": None, "mid_pnl": None, "net_return": None,
        "annualized": None, "max_contracts": None, "total_gain": None,
        "poly_volume": None, "days_to_expiry": None,
        "kalshi_title": None, "kalshi_rules": None, "kalshi_yes_label": None,
        "kalshi_no_label": None, "poly_question": None,
        "poly_description": None, "image": None,
        "window_start": None, "window_close": None,
        "minutes_to_resolve": None,
    }
