"""Kalshi public trade API client (no auth required for market data).

Kalshi rate-limits aggressively, so we do NOT fetch per ticker. Instead:

  fetch_quotes()  -> ONE batched GET /markets?tickers=a,b,c... (chunked +
                      cursor-paged) for prices/status/expiry of every ticker.
  fetch_sizes()   -> /markets/{t}/orderbook, called only for the small set of
                      basis-favorable candidates, to get true executable
                      top-of-book size.

Kalshi binary markets: buying YES is matched against resting NO bids, so the
size available at the YES ask == size of the best NO bid (and vice versa).
"""
import re
from .net import get_json, parallel, FetchError

BASE = "https://api.elections.kalshi.com/trade-api/v2"
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
        "yes_ask_size": None,
        "no_ask_size": None,
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


def _best_bid_size(ladder):
    """ladder = [[price, size], ...] resting bids -> size at the top bid."""
    best = None
    for row in ladder or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if best is None or p > best[0]:
            best = (p, s)
    return best[1] if best else None


def _one_ob(ticker):
    ob = get_json("%s/markets/%s/orderbook" % (BASE, ticker)).get(
        "orderbook_fp", {})
    return {
        "yes_ask_size": _best_bid_size(ob.get("no_dollars")),
        "no_ask_size": _best_bid_size(ob.get("yes_dollars")),
    }


def fetch_sizes(tickers):
    """Top-of-book executable size for a SMALL candidate set. {ticker: {..}}."""
    uniq = sorted({t for t in tickers if t})
    res = parallel(_one_ob, uniq, workers=6)
    return {t: (None if isinstance(v, FetchError) else v)
            for t, v in res.items()}
