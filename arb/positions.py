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


def _paired(poly_pos, kalshi_pos):
    pairs = _load_pairs()
    by_slug, by_tkr = {}, {}
    for pr in pairs:
        if pr.get("poly_slug"):
            by_slug.setdefault(pr["poly_slug"], pr)
        if pr.get("kalshi_ticker"):
            by_tkr[pr["kalshi_ticker"]] = pr

    groups = {}

    def key(pr):
        return "%s|%s|%s" % (pr.get("asset"), pr.get("kalshi_ticker"),
                             pr.get("poly_slug"))

    for p in poly_pos:
        pr = by_slug.get(p["ref"])
        if pr:
            groups.setdefault(key(pr), {"pair": pr, "poly": [], "kalshi": []})
            groups[key(pr)]["poly"].append(p)
    for p in kalshi_pos:
        pr = by_tkr.get(p["ref"])
        if pr:
            groups.setdefault(key(pr), {"pair": pr, "poly": [], "kalshi": []})
            groups[key(pr)]["kalshi"].append(p)

    rows = []
    for g in groups.values():
        legs = g["poly"] + g["kalshi"]
        cost = sum((x["cost"] or 0) for x in legs)
        value = sum((x["value"] or 0) for x in legs)
        pnl = sum((x["pnl"] or 0) for x in legs if x["pnl"] is not None)
        rows.append({
            "asset": g["pair"].get("asset"),
            "kalshi_ticker": g["pair"].get("kalshi_ticker"),
            "poly_slug": g["pair"].get("poly_slug"),
            "kalshi_strike": g["pair"].get("kalshi_strike"),
            "poly_strike": g["pair"].get("poly_strike"),
            "legs": legs,
            "cost": cost,
            "value": value,
            "pnl": pnl,
            "pnl_pct": (pnl / cost if cost else None),
            "complete": bool(g["poly"] and g["kalshi"]),
        })
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return rows


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
