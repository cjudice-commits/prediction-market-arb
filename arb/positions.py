"""Live positions from Polymarket (public, by wallet) and Kalshi (signed).

Polymarket is on-chain: the public Data API returns holdings + P&L given
just the proxy-wallet address — no secret.

Kalshi requires an authed call. We sign exactly per Kalshi's spec
(RSA-PSS / SHA-256 over "{ts}{METHOD}{path}") but shell out to the system
`openssl` so no crypto dependency and the private key never leaves disk.
Read-only — no order endpoints are ever called.

Credentials come from data/secrets.json (git-ignored), supplied by the user.
Missing/!configured venues degrade gracefully with setup guidance.
"""
import base64
import datetime
import json
import os
import re
import subprocess
import time
import urllib.request
import urllib.error

from .net import get_json, parallel, FetchError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(ROOT, "data", "secrets.json")
PAIRS = os.path.join(ROOT, "data", "pairs.json")

POLY_DATA = "https://data-api.polymarket.com"
CLOB_MKT = "https://clob.polymarket.com/markets/"
KALSHI = "https://api.elections.kalshi.com"
KALSHI_POS_PATH = "/trade-api/v2/portfolio/positions"

_ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE", "ZEC", "LTC",
           "ADA", "AVAX", "LINK", "SHIB", "XLM", "SUI", "TRX")


def _asset_from_ticker(t):
    """KXXRPMAXMON-XRP-26MAY31-180 -> XRP ; KXHYPED-26MAY0117-T31 -> HYPE."""
    if not t:
        return None
    parts = t.split("-")
    if len(parts) > 1 and parts[1].isalpha() and 2 <= len(parts[1]) <= 5:
        return parts[1].upper()
    head = parts[0].upper()
    for a in _ASSETS:
        if a in head:
            return a
    return None


# Polymarket slugs spell the asset out ("will-bitcoin-…"); map name -> symbol so
# poly legs carry an asset for the paired view (Kalshi legs get it from ticker).
_POLY_ASSET = [
    ("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL"),
    ("ripple", "XRP"), ("dogecoin", "DOGE"), ("hyperliquid", "HYPE"),
    ("binance", "BNB"), ("litecoin", "LTC"), ("cardano", "ADA"),
    ("avalanche", "AVAX"), ("chainlink", "LINK"), ("stellar", "XLM"),
    ("zcash", "ZEC"), ("shiba", "SHIB"),
    # short forms / tickers that also appear in slugs
    ("btc", "BTC"), ("eth", "ETH"), ("sol", "SOL"), ("xrp", "XRP"),
    ("doge", "DOGE"), ("bnb", "BNB"), ("hype", "HYPE"), ("zec", "ZEC"),
    ("sui", "SUI"), ("trx", "TRX"), ("xlm", "XLM"),
]


def _asset_from_slug(slug):
    s = (slug or "").lower()
    for name, sym in _POLY_ASSET:
        if name in s:
            return sym
    return None


def load_secrets():
    try:
        with open(SECRETS) as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- Polymarket
def _poly(wallet):
    base = "%s/positions?user=%s&sizeThreshold=0.1&limit=500&sortBy=CURRENT&sortDirection=DESC" % (
        POLY_DATA, wallet)
    rows = get_json(base)
    raw = [p for p in (rows or []) if abs(float(p.get("size") or 0)) >= 1e-9]

    # Value each holding at the current CLOB best BID (executable exit price),
    # not the data-API curPrice (last/mid). Fall back to the API fields only
    # if a token has no live bid (illiquid / resolved).
    from . import poly
    bids = {}
    try:
        bids = poly.fetch_token_bids([p.get("asset") for p in raw])
    except Exception:
        bids = {}

    out = []
    for p in raw:
        size = float(p.get("size") or 0)
        cost = p.get("initialValue")
        bid = bids.get(str(p.get("asset")))
        if bid is not None:
            cur_price = bid
            value = size * bid
            pnl = (value - cost) if cost is not None else None
            pnl_pct = (pnl / cost) if (pnl is not None and cost) else None
        else:                                   # no live bid -> API fallback
            cur_price = p.get("curPrice")
            value = p.get("currentValue")
            pnl = p.get("cashPnl")
            pnl_pct = (p.get("percentPnl") / 100.0
                       if p.get("percentPnl") is not None else None)
        out.append({
            "venue": "Polymarket",
            "asset": _asset_from_slug(p.get("slug")) or _asset_from_slug(p.get("title")),
            "market": p.get("title"),
            "ref": p.get("slug"),
            "side": p.get("outcome"),
            "size": size,
            "avg_price": p.get("avgPrice"),
            "cur_price": cur_price,
            "cost": cost,
            "value": value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "realized_pnl": p.get("realizedPnl"),
            "icon": p.get("icon"),
            "end_date": (p.get("endDate") or "")[:10] or None,
            "redeemable": bool(p.get("redeemable")),
            "asset_id": p.get("asset"),
            "condition_id": p.get("conditionId"),
        })
    # Portfolio value coherent with bid-based marks; API /value as fallback.
    total = sum((p["value"] or 0) for p in out) if out else None
    if total is None:
        try:
            v = get_json("%s/value?user=%s" % (POLY_DATA, wallet))
            if isinstance(v, list) and v:
                total = v[0].get("value")
        except FetchError:
            pass
    return {"configured": True, "positions": out, "total_value": total}


def _clob_resolution(cid):
    """CLOB market by conditionId -> (closed, winning_outcome|None)."""
    m = get_json(CLOB_MKT + cid)
    if not isinstance(m, dict) or not m.get("closed"):
        return (False, None)
    win = None
    for tk in m.get("tokens") or []:
        if tk.get("winner"):
            win = tk.get("outcome")
    return (True, win)


def _poly_history(wallet, open_cids):
    """Reconstruct realized P&L for resolved markets from the public activity
    ledger. Trade cash is exact; the redemption payout is derived as
    (net contracts held on CLOB's winning outcome) x $1."""
    acts = []
    for off in range(0, 4000, 500):                 # paginate, bounded
        try:
            page = get_json("%s/activity?user=%s&limit=500&offset=%d"
                            % (POLY_DATA, wallet, off))
        except FetchError:
            break
        if not page:
            break
        acts.extend(page)
        if len(page) < 500:
            break

    mk = {}
    for a in acts:
        if a.get("type") != "TRADE":
            continue
        cid = a.get("conditionId")
        if not cid or cid in open_cids:
            continue
        g = mk.setdefault(cid, {
            "title": a.get("title"), "slug": a.get("slug"),
            "icon": a.get("icon"), "net": {}, "buy": 0.0, "sell": 0.0,
            "ts": a.get("timestamp")})
        oc = a.get("outcome")
        sz = float(a.get("size") or 0)
        usd = float(a.get("usdcSize") or 0)
        if a.get("side") == "BUY":
            g["net"][oc] = g["net"].get(oc, 0.0) + sz
            g["buy"] += usd
        else:
            g["net"][oc] = g["net"].get(oc, 0.0) - sz
            g["sell"] += usd
        g["ts"] = max(g["ts"] or 0, a.get("timestamp") or 0)

    res = parallel(_clob_resolution, list(mk), workers=12)
    rows = []
    import datetime
    for cid, g in mk.items():
        r = res.get(cid)
        closed = bool(r and not isinstance(r, FetchError) and r[0])
        winner = (r[1] if closed else None)        # CLOB outcome that won
        held = max(0.0, g["net"].get(winner, 0.0)) if winner else 0.0
        payout = held                              # winning shares pay $1
        trade_cash = g["sell"] - g["buy"]
        realized = trade_cash + payout
        # Side = the outcome you carried net into resolution / out the door.
        # When fully sold (all nets ~0), there's no meaningful side.
        nz = {k: v for k, v in (g["net"] or {}).items() if abs(v) > 1e-9}
        side = (max(nz.items(), key=lambda kv: abs(kv[1]))[0]
                if nz else None)
        sd = (datetime.datetime.utcfromtimestamp(g["ts"]).date().isoformat()
              if g.get("ts") else None)
        # Result label: winning outcome if resolved; "SOLD" if user exited
        # before resolution. Both are legitimate "history" rows.
        if closed:
            result = (winner.upper() if winner else "VOID")
        else:
            result = "SOLD"
        rows.append({
            "venue": "Polymarket",
            "market": g["title"],
            "ref": g["slug"],
            "result": result,
            "side": side,
            "size": abs(g["net"].get(side, 0.0)) if side else 0.0,
            "cost": g["buy"],
            "payout": payout + max(0.0, g["sell"]),
            "realized": realized,
            "settled_date": sd,
            "icon": g["icon"],
            "derived": True,
        })
    rows.sort(key=lambda x: x["settled_date"] or "", reverse=True)
    return rows


# -------------------------------------------------------------------- Kalshi
def _sign(message, key_path):
    """RSA-PSS / SHA-256, salt = digest length — Kalshi's scheme, via openssl."""
    p = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", key_path,
         "-sigopt", "rsa_padding_mode:pss",
         "-sigopt", "rsa_pss_saltlen:-1"],
        input=message.encode(), capture_output=True)
    if p.returncode != 0:
        raise FetchError("openssl sign failed: %s"
                         % p.stderr.decode()[:160].strip())
    return base64.b64encode(p.stdout).decode()


def _kalshi(key_id, key_path):
    if not key_id or not key_path:
        return {"configured": False, "reason": "no_credentials"}
    if not os.path.isfile(os.path.expanduser(key_path)):
        return {"configured": False, "reason": "key_file_not_found"}
    key_path = os.path.expanduser(key_path)

    def signed_get(path):
        ts = str(int(time.time() * 1000))
        sig = _sign(ts + "GET" + path.split("?")[0], key_path)
        req = urllib.request.Request(KALSHI + path, headers={
            "KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "Accept": "application/json",
            "User-Agent": "prediction-market-arb",
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    try:
        data = signed_get(KALSHI_POS_PATH + "?limit=500&count_filter=position")
    except urllib.error.HTTPError as e:
        return {"configured": True, "error": "Kalshi HTTP %s — check key ID / "
                "private key / permissions." % e.code, "positions": []}
    except Exception as e:
        return {"configured": True,
                "error": "%s" % type(e).__name__, "positions": []}

    mp = data.get("market_positions") or []
    tickers = [m.get("ticker") for m in mp if m.get("ticker")]
    marks = {}
    if tickers:
        try:
            from . import kalshi
            marks = kalshi.fetch_quotes(tickers)
        except Exception:
            marks = {}

    def num(m, base):
        """Kalshi returns "{base}_dollars" as a string; fall back to int cents."""
        d = m.get(base + "_dollars")
        if d is not None:
            try:
                return float(d)
            except (TypeError, ValueError):
                pass
        v = m.get(base)
        return (v / 100.0) if isinstance(v, (int, float)) else 0.0

    out = []
    for m in mp:
        pos = 0.0
        for f in ("position_fp", "position"):  # fp = fractional position
            try:
                pos = float(m.get(f))
                break
            except (TypeError, ValueError):
                continue
        if abs(pos) < 1e-9:
            continue
        side = "Yes" if pos > 0 else "No"        # negative == net No
        size = abs(pos)
        q = marks.get(m.get("ticker")) or {}
        mark = q.get("yes_bid") if pos > 0 else q.get("no_bid")
        # Cost basis of the CURRENTLY held position = market_exposure.
        # total_traded is lifetime traded volume (inflated by any round-trips)
        # and must NOT be used as cost — it shows phantom losses.
        cost = num(m, "market_exposure") or num(m, "total_traded")
        value = (size * mark) if mark is not None else cost
        out.append({
            "venue": "Kalshi",
            "market": q.get("title") or m.get("ticker"),
            "ref": m.get("ticker"),
            "side": side,
            "size": size,
            "avg_price": (cost / size) if size else None,
            "cur_price": mark,
            "cost": cost or None,
            "value": value,
            "pnl": (value - cost) if cost else None,
            "pnl_pct": ((value - cost) / cost) if cost else None,
            "realized_pnl": num(m, "realized_pnl"),
            "fees": num(m, "fees_paid"),
            "icon": None,
            "asset": _asset_from_ticker(m.get("ticker")),
            "end_date": q.get("expiry") or None,
            "redeemable": q.get("status") in ("settled", "finalized"),
            "asset_id": m.get("ticker"),
        })

    total_value = None
    try:
        pv = signed_get("/trade-api/v2/portfolio/balance").get(
            "portfolio_value")
        if isinstance(pv, (int, float)):
            total_value = pv / 100.0      # cents -> dollars
    except Exception:
        pass
    if total_value is None:
        total_value = sum(p["value"] or 0 for p in out)

    # Settled (expired) markets — exact realized P&L straight from Kalshi.
    settled, cursor = [], ""
    for _ in range(15):                  # cursor-page guard
        try:
            sd = signed_get("/trade-api/v2/portfolio/settlements?limit=200"
                            + ("&cursor=" + cursor if cursor else ""))
        except Exception:
            break
        for s in sd.get("settlements", []):
            yc = float(s.get("yes_total_cost_dollars") or 0)
            nc = float(s.get("no_total_cost_dollars") or 0)
            fee = float(s.get("fee_cost") or 0)
            yct = float(s.get("yes_count_fp") or 0)
            nct = float(s.get("no_count_fp") or 0)
            result = (s.get("market_result") or "").lower()
            # Kalshi's `revenue`/`value` are 0 when both sides were held to
            # expiry — NOT the payout. Settled contracts pay $1 each on the
            # winning side, so derive payout from market_result + counts.
            if result == "yes":
                payout = yct
            elif result == "no":
                payout = nct
            else:                           # void/refund -> fall back
                payout = (s.get("revenue") or 0) / 100.0
            cost = yc + nc + fee
            side = "Yes" if yct >= nct else "No"
            settled.append({
                "venue": "Kalshi",
                "market": s.get("ticker"),
                "ref": s.get("ticker"),
                "result": (result.upper() or "—"),
                "side": side,
                "size": max(yct, nct),
                "cost": cost,
                "payout": payout,
                "realized": payout - cost,
                "settled_date": (s.get("settled_time") or "")[:10] or None,
                "asset": _asset_from_ticker(s.get("ticker")),
                "derived": False,
            })
        cursor = sd.get("cursor") or ""
        if not cursor:
            break

    # Sold-out / round-tripped markets: Kalshi's `/settlements` only lists
    # markets the user held to expiry. Anything they bought and then sold
    # before expiry is captured only on the *positions* endpoint with
    # count_filter=total_traded (and position_fp==0). realized_pnl_dollars
    # there is Kalshi's exact booked P&L for the round-trip.
    settled_tickers = {s["ref"] for s in settled}
    try:
        sd2 = signed_get(KALSHI_POS_PATH +
                         "?limit=500&count_filter=total_traded")
        for m in sd2.get("market_positions", []):
            tk = m.get("ticker")
            if not tk or tk in settled_tickers:
                continue                      # already in settlements
            try:
                pos_fp = float(m.get("position_fp") or 0)
            except (TypeError, ValueError):
                pos_fp = 0.0
            if abs(pos_fp) > 1e-9:
                continue                      # currently held; in `out`
            if float(m.get("total_traded_dollars") or 0) <= 0:
                continue
            settled.append({
                "venue": "Kalshi",
                "market": tk,
                "ref": tk,
                "result": "SOLD",
                "side": None,
                "size": None,
                "cost": None,
                "payout": None,
                "realized": num(m, "realized_pnl"),
                "settled_date": (m.get("last_updated_ts") or "")[:10] or None,
                "asset": _asset_from_ticker(tk),
                "derived": False,
            })
    except Exception:
        pass                                  # don't break settlements

    return {"configured": True, "positions": out,
            "total_value": total_value, "settlements": settled}


# ------------------------------------------------------------- paired view
def _load_pairs():
    try:
        with open(PAIRS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _kalshi_dir(tkr):
    t = (tkr or "").upper()
    if "MAXMON" in t or "MAX" in t:
        return "above"
    if "MINMON" in t or "MIN" in t:
        return "below"
    return None


def _poly_dir(slug):
    s = (slug or "").lower()
    if "reach" in s or "hit" in s or "above" in s:
        return "above"
    if "dip" in s or "below" in s:
        return "below"
    return None


def _pays_high(direc, side):
    """A leg 'pays high' if it settles $1 when the asset ends ABOVE its strike.
    above+YES and below+NO pay high; above+NO and below+YES pay low."""
    if direc is None:
        return None
    s = str(side or "").strip().lower()
    if s in ("yes", "y", "up", "long"):
        yes = True
    elif s in ("no", "n", "down", "short"):
        yes = False
    else:
        return None
    return yes if direc == "above" else (not yes)


def _kalshi_strike(tkr, lookup=None):
    if lookup and lookup.get(tkr):
        return lookup[tkr]
    m = re.search(r"-(\d+)$", tkr or "")
    return float(m.group(1)) / 100.0 if m else None


def _poly_strike(slug, lookup=None):
    if lookup and lookup.get(slug):
        return lookup[slug]
    s = (slug or "").lower()
    m = re.search(r"(?:reach|hit|dip-to|dip|above|below)-(\d+(?:pt\d+)?)(k?)", s)
    if not m:
        return None
    num = float(m.group(1).replace("pt", "."))
    return num * 1000.0 if m.group(2) == "k" else num


def _fmt_strike(v):
    if v is None:
        return "?"
    if v >= 1000:
        return "%gk" % (v / 1000.0)
    return "%g" % v


def _exp_key(leg):
    """Group key by settlement month. Kalshi/Poly end_date is the day AFTER the
    month being settled (00:00 UTC of the 1st), so step back a day first."""
    d = (leg.get("end_date") or "")[:10]
    try:
        y, m, dd = (int(x) for x in d.split("-"))
        dt = datetime.date(y, m, dd) - datetime.timedelta(days=1)
        return "%04d-%02d" % (dt.year, dt.month)
    except (ValueError, TypeError):
        return "?"


def _classify(leg, tkr_strike, slug_strike):
    if leg.get("venue") == "Kalshi":
        direc = _kalshi_dir(leg.get("ref"))
        leg["_strike"] = _kalshi_strike(leg.get("ref"), tkr_strike)
    else:
        direc = _poly_dir(leg.get("ref"))
        leg["_strike"] = _poly_strike(leg.get("ref"), slug_strike)
    leg["_dir"] = direc
    leg["_pays"] = _pays_high(direc, leg.get("side"))
    return leg


def _row_from_legs(legs, kind, complete, note, asset, expiry,
                   low_strike=None, high_strike=None):
    cost = sum((x.get("cost") or 0) for x in legs)
    value = sum((x.get("value") or 0) for x in legs)
    pnl = sum((x.get("pnl") or 0) for x in legs if x.get("pnl") is not None)
    return {
        "asset": asset, "kind": kind, "complete": complete, "note": note,
        "expiry": expiry, "low_strike": low_strike, "high_strike": high_strike,
        "legs": legs, "cost": cost, "value": value, "pnl": pnl,
        "pnl_pct": (pnl / cost if cost else None),
    }


def _slice_leg(leg, qty):
    """A view of `leg` holding only `qty` contracts, with cost/value/pnl
    pro-rated. Used when one leg's size is split across several hedge rows."""
    s = float(leg.get("size") or 0)
    if s <= 0 or qty is None or abs(qty - s) < 1e-9:
        return leg                             # whole leg — no split needed
    frac = qty / s
    out = dict(leg)
    out["size"] = qty
    for k in ("cost", "value", "pnl"):
        v = leg.get(k)
        out[k] = (v * frac) if isinstance(v, (int, float)) else v
    return out


def _match_bucket(legs, asset, expiry):
    """Quantity-aware pairing of pays-low against pays-high legs across venues.
    A hedge holds one of each: pays-low covers the downside, pays-high the up.

      strikes equal     -> matched (clean barrier hedge)
      low strike > high  -> basis+  (overlap band where BOTH legs win)
      low strike < high  -> basis-  (gap band where NEITHER wins = basis risk)

    Contracts are filled by size: exact-strike (matched, gap 0) pairs sort and
    fill first, so a leg's excess only spills into a basis pair once every
    matched pairing is exhausted. Each fill consumes min(remaining) contracts
    from both legs; a partially used leg's economics are pro-rated. Cross-venue
    only; reject absurd gaps (unfavorable > 10% of level, overlap > 25%)."""
    lows = [l for l in legs if l.get("_pays") is False and l.get("_strike")]
    highs = [l for l in legs if l.get("_pays") is True and l.get("_strike")]
    other = [l for l in legs if l.get("_pays") is None or not l.get("_strike")]

    rem_lo = [float(l.get("size") or 0) for l in lows]
    rem_hi = [float(l.get("size") or 0) for l in highs]

    cands = []
    for li, lo in enumerate(lows):
        for hi, hg in enumerate(highs):
            if lo.get("venue") == hg.get("venue"):
                continue                       # a real hedge spans both venues
            ls, hs = lo["_strike"], hg["_strike"]
            level = max(ls, hs) or 1.0
            gap = hs - ls
            if gap > 0 and gap > 0.10 * level:
                continue                       # basis- gap too wide to be a hedge
            if gap < 0 and (-gap) > 0.25 * level:
                continue                       # basis+ overlap implausibly large
            cands.append((abs(ls - hs), li, hi))
    cands.sort()                               # gap 0 (matched) first, then nearest

    rows = []
    for _, li, hi in cands:
        q = min(rem_lo[li], rem_hi[hi])
        if q <= 1e-9:
            continue                           # one side already fully consumed
        rem_lo[li] -= q
        rem_hi[hi] -= q
        lo, hg = lows[li], highs[hi]
        ls, hs = lo["_strike"], hg["_strike"]
        if abs(ls - hs) < 1e-9:
            kind = "matched"
            note = "hedged at %s" % _fmt_strike(ls)
        elif ls > hs:
            kind = "basis+"
            note = ("overlap %s-%s — both legs win in the gap"
                    % (_fmt_strike(hs), _fmt_strike(ls)))
        else:
            kind = "basis-"
            note = ("gap %s-%s — neither leg wins between (basis risk)"
                    % (_fmt_strike(ls), _fmt_strike(hs)))
        rows.append(_row_from_legs([_slice_leg(hg, q), _slice_leg(lo, q)],
                                   kind, True, note, asset, expiry,
                                   low_strike=ls, high_strike=hs))

    leftover = [_slice_leg(lo, rem_lo[i]) for i, lo in enumerate(lows)
                if rem_lo[i] > 1e-9]
    leftover += [_slice_leg(hg, rem_hi[i]) for i, hg in enumerate(highs)
                 if rem_hi[i] > 1e-9]
    leftover += other
    return rows, leftover


def _single_row(leg):
    pays = leg.get("_pays")
    strike = leg.get("_strike")
    if pays is True and strike is not None:
        note = "uncovered — pays only above %s" % _fmt_strike(strike)
    elif pays is False and strike is not None:
        note = "uncovered — pays only below %s" % _fmt_strike(strike)
    else:
        note = "unclassified leg"
    return _row_from_legs([leg], "single", False, note,
                          leg.get("asset"), _exp_key(leg))


def _paired(poly_pos, kalshi_pos):
    """Recognize economic hedges from live holdings, not just curated pairs.

    Curated pairs.json (if present) only supplies authoritative strikes; the
    matching itself keys off each leg's economics so any cross-venue,
    opposite-direction hedge in the book is surfaced — matched or basis."""
    pairs = _load_pairs()
    tkr_strike = {p["kalshi_ticker"]: p.get("kalshi_strike")
                  for p in pairs if p.get("kalshi_ticker")}
    slug_strike = {p["poly_slug"]: p.get("poly_strike")
                   for p in pairs if p.get("poly_slug")}

    # Copy each leg so _classify's _pays/_strike/_dir scratch keys don't leak
    # into the dicts returned under "polymarket"/"kalshi".
    legs = [_classify(dict(p), tkr_strike, slug_strike)
            for p in list(poly_pos) + list(kalshi_pos)]

    buckets = {}
    for l in legs:
        buckets.setdefault((l.get("asset"), _exp_key(l)), []).append(l)

    pair_rows, single_rows = [], []
    for (asset, expiry), blegs in buckets.items():
        pr, leftover = _match_bucket(blegs, asset, expiry)
        pair_rows.extend(pr)
        single_rows.extend(_single_row(l) for l in leftover)

    pair_rows.sort(key=lambda r: (r["pnl"] if r["pnl"] is not None else 0),
                   reverse=True)
    single_rows.sort(key=lambda r: (r["asset"] or "z", -(r["pnl"] or 0)))
    return pair_rows + single_rows


def run_positions():
    s = load_secrets()
    wallet = (s.get("polymarket_wallet") or "").strip()
    poly = ({"configured": False, "reason": "no_wallet"}
            if not wallet or wallet.startswith("0xYOUR")
            else _safe(_poly, wallet))
    kalshi = _safe(_kalshi, s.get("kalshi_key_id"),
                   s.get("kalshi_private_key_path"))

    pp = poly.get("positions", []) if poly.get("configured") else []
    kp = kalshi.get("positions", []) if kalshi.get("configured") else []
    paired = _paired(pp, kp)

    # History (expired/settled): Kalshi exact; Polymarket reconstructed.
    k_hist = kalshi.get("settlements", []) if kalshi.get("configured") else []
    p_hist = []
    if poly.get("configured"):
        open_cids = {p.get("condition_id") for p in pp if p.get("condition_id")}
        p_hist = _safe(_poly_history, wallet, open_cids)
        if isinstance(p_hist, dict):       # _safe wrapped an error
            p_hist = []

    def total_pnl(lst):
        return sum((p.get("pnl") or 0) for p in lst)

    def total_cost(lst):
        return sum((p.get("cost") or 0) for p in lst)

    def total_realized(lst):
        return sum((p.get("realized") or 0) for p in lst)

    poly_pnl = total_pnl(pp) if poly.get("configured") else None
    kalshi_pnl = total_pnl(kp) if kalshi.get("configured") else None
    grand_pnl = (None if poly_pnl is None and kalshi_pnl is None
                 else (poly_pnl or 0) + (kalshi_pnl or 0))
    all_cost = total_cost(pp) + total_cost(kp)
    total_return = ((grand_pnl / all_cost)
                    if (grand_pnl is not None and all_cost) else None)

    kalshi_realized = total_realized(k_hist) if kalshi.get("configured") else None
    poly_realized = total_realized(p_hist) if poly.get("configured") else None
    grand_realized = (None if kalshi_realized is None and poly_realized is None
                      else (kalshi_realized or 0) + (poly_realized or 0))

    return {
        "polymarket": poly,
        "kalshi": kalshi,
        "paired": paired,
        "history": {"kalshi": k_hist, "polymarket": p_hist},
        "totals": {
            "poly_value": poly.get("total_value"),
            "kalshi_value": kalshi.get("total_value"),
            "poly_pnl": poly_pnl,
            "kalshi_pnl": kalshi_pnl,
            "total_pnl": grand_pnl,
            "total_return": total_return,
            "kalshi_realized": kalshi_realized,
            "poly_realized": poly_realized,
            "total_realized": grand_realized,
            "open_positions": len(pp) + len(kp),
            "settled_count": len(k_hist) + len(p_hist),
            "paired_count": sum(1 for r in paired if r["complete"]),
        },
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }


def _safe(fn, *a):
    try:
        return fn(*a)
    except FetchError as e:
        return {"configured": True, "error": str(e), "positions": []}
    except Exception as e:
        return {"configured": True,
                "error": "%s: %s" % (type(e).__name__, e), "positions": []}
