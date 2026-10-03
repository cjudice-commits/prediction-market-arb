"""Kalshi public trade API client (no auth required for market data).

Kalshi rate-limits aggressively, so we do NOT fetch per ticker. Instead:

  fetch_quotes()  -> ONE batched GET /markets?tickers=a,b,c... (chunked +
                      cursor-paged) for prices/status/expiry of every ticker.
  fetch_sizes()   -> /markets/{t}/orderbook, called only for the small set of
                      basis-favorable candidates, to get the true executable
                      ask ladder (all levels, not just top of book).

Kalshi binary markets: buying YES is matched against resting NO bids, so the
size available at the YES ask == size of the best NO bid (and vice versa).
"""
import re
from .net import get_json, parallel, FetchError, KALSHI_HOST

BASE = KALSHI_HOST + "/trade-api/v2"
_CHUNK = 80

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _f(v):
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _ask(v):
    """Like _f, but also drops the '1.0000' sentinel Kalshi returns when there
    is no resting offer on that side. Real Kalshi asks are 1-99c, so a $1.00
    ask is never executable and must not be treated as a tradeable quote."""
    x = _f(v)
    return x if (x is not None and x < 1.0) else None


def parse_expiry(ticker):
    """KX...-26MAY31-7000 -> '2026-05-31', else None."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})-", ticker)
    if not m or m.group(2) not in _MONTHS:
        return None
    return "20%s-%02d-%02d" % (m.group(1), _MONTHS[m.group(2)], int(m.group(3)))


def _clip(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _quote(mkt):
    t = mkt.get("ticker")
    return {
        "ticker": t,
        "yes_bid": _f(mkt.get("yes_bid_dollars")),
        "yes_ask": _ask(mkt.get("yes_ask_dollars")),
        "no_bid": _f(mkt.get("no_bid_dollars")),
        "no_ask": _ask(mkt.get("no_ask_dollars")),
        # Depth comes from the orderbook endpoint (fetch_sizes), not /markets.
        "yes_ask_size": None,
        "no_ask_size": None,
        "yes_asks": None,
        "no_asks": None,
        "open_interest": _f(mkt.get("open_interest_fp")) or 0.0,
        "status": mkt.get("status"),
        "expiry": (mkt.get("close_time") or "")[:10] or parse_expiry(t or ""),
        "title": mkt.get("title"),
        "yes_label": mkt.get("yes_sub_title"),
        "no_label": mkt.get("no_sub_title"),
        "rules": _clip(mkt.get("rules_primary"), 360),
    }


def fetch_quotes(tickers):
    """tickers: iterable. Returns {ticker: quote|None} via batched calls."""
    uniq = sorted({t for t in tickers if t})
    out = {t: None for t in uniq}
    for i in range(0, len(uniq), _CHUNK):
        chunk = uniq[i:i + _CHUNK]
        cursor = ""
        for _ in range(10):  # cursor-page guard
            url = "%s/markets?limit=1000&tickers=%s" % (BASE, ",".join(chunk))
            if cursor:
                url += "&cursor=" + cursor
            try:
                data = get_json(url)
            except FetchError:
                break
            for m in data.get("markets", []):
                if m.get("ticker") in out:
                    out[m["ticker"]] = _quote(m)
            cursor = data.get("cursor") or ""
            if not cursor:
                break
    return out


def _ask_ladder(bids):
    """Resting bids on the OPPOSITE side -> this side's ask ladder.

    bids = [[price, size], ...]. Buying YES is matched against resting NO bids,
    so a NO bid at q is an offer of YES at 1-q. Returns [(ask_price, size), ...]
    ascending by ask price (best first) — i.e. descending by bid price.

    A bid at 0 would imply a $1.00 ask, which is never executable; that's the
    same sentinel `_ask` drops on the /markets side, so filter it here too.
    """
    out = []
    for row in bids or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0 and 0.0 < p < 1.0:
            out.append((1.0 - p, s))
    out.sort(key=lambda x: x[0])
    return out


def _bid_ladder(bids):
    """This side's resting bid ladder, [(price, size), ...] DESCENDING by price
    (best first) — what you could SELL into, level by level.

    The mirror of `_ask_ladder`: buying YES crosses the resting NO bids, but
    *selling* YES crosses the resting YES bids, so this reads the same-side
    ladder directly rather than converting it. Keeping every level (not just the
    top) is what lets an exit be priced by real depth: a position far larger than
    the best bid walks DOWN this ladder, getting worse fills the whole way.
    """
    out = []
    for row in bids or []:
        try:
            px, sz = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if sz > 0 and 0.0 < px < 1.0:
            out.append((px, sz))
    out.sort(key=lambda x: -x[0])
    return out


def _one_ob(ticker):
    ob = get_json("%s/markets/%s/orderbook" % (BASE, ticker)).get(
        "orderbook_fp", {})
    yl = _ask_ladder(ob.get("no_dollars"))
    nl = _ask_ladder(ob.get("yes_dollars"))
    return {
        "yes_ask_size": yl[0][1] if yl else None,
        "no_ask_size": nl[0][1] if nl else None,
        "yes_asks": yl,
        "no_asks": nl,
        # Sell-side depth, for exiting a held position.
        "yes_bids": _bid_ladder(ob.get("yes_dollars")),
        "no_bids": _bid_ladder(ob.get("no_dollars")),
    }


def fetch_sizes(tickers):
    """Top-of-book executable size for a SMALL candidate set. {ticker: {..}}."""
    uniq = sorted({t for t in tickers if t})
    res = parallel(_one_ob, uniq, workers=6)
    return {t: (None if isinstance(v, FetchError) else v)
            for t, v in res.items()}
