"""Build a read-only 'what would be sent' trade ticket for ONE arb pair.

  *** THIS MODULE PLACES NO ORDERS. ***

It only reads live quotes (the same public / read-only calls the scanner
already makes) and computes the exact pair of orders a cross-venue hedge would
require — side, size, limit price, capital, and guaranteed worst-case — so the
user can preview a trade before any execution path is ever wired up.

No Kalshi/Polymarket *order* endpoints are touched anywhere in this file.
"""
import time

from . import calc, kalshi, poly, scan


def _int(x):
    return int(x) if x is not None else None


def run_prepare(ticker, slug, max_size=None, buffer_cents=1.0):
    """Return a preview ticket dict for the pair (ticker, slug). Read-only."""
    if not ticker or not slug:
        return {"error": "ticker and slug are required"}

    pair = next((p for p in scan.load_pairs()
                 if p.get("kalshi_ticker") == ticker
                 and p.get("poly_slug") == slug), None)
    if not pair:
        return {"error": "pair not found in pairs.json"}
    settings = scan.load_settings()

    # Fresh, read-only quotes for just this one pair.
    kq = kalshi.fetch_quotes([ticker]).get(ticker)
    pq = poly.fetch_quotes([slug]).get(slug)
    # Real Kalshi top-of-book ask size (the scan uses open interest as a proxy
    # off-candidate; for a trade ticket we want the executable size).
    szs = (kalshi.fetch_sizes([ticker]) or {}).get(ticker)
    if szs and kq:
        if szs.get("yes_ask_size") is not None:
            kq["yes_ask_size"] = szs["yes_ask_size"]
        if szs.get("no_ask_size") is not None:
            kq["no_ask_size"] = szs["no_ask_size"]

    row = calc.evaluate(pair, kq, pq, settings)
    if not row.get("best_side"):
        return {"error": "no executable hedge right now",
                "status": row.get("status"), "asset": row.get("asset")}

    buf = (buffer_cents or 0.0) / 100.0

    # best_side "YES+NO" = Kalshi YES + Poly NO ; "NO+YES" = Kalshi NO + Poly YES
    k_side, p_side = (("yes", "no") if row["best_side"] == "YES+NO"
                      else ("no", "yes"))
    k_ask, p_ask = row.get("kalshi_price"), row.get("poly_price")
    # Marketable limit = ask + buffer, capped below $1. Kalshi ticks in cents,
    # Polymarket finer — round each to its grid.
    k_limit = min(0.99, round(k_ask + buf, 2)) if k_ask is not None else None
    p_limit = min(0.999, round(p_ask + buf, 3)) if p_ask is not None else None

    avail = row.get("max_contracts")
    size, capped = avail, None
    if max_size and avail is not None and max_size < avail:
        size, capped = max_size, "your max"
    size = _int(size)

    ksz, psz = row.get("kalshi_size"), row.get("poly_size")
    thin = None
    if ksz is not None and psz is not None:
        thin = "Kalshi" if ksz <= psz else "Polymarket"

    k_cost = size * k_ask if (size and k_ask is not None) else None
    p_cost = size * p_ask if (size and p_ask is not None) else None
    capital = (k_cost or 0) + (p_cost or 0) if size else None

    w_ct = row.get("worst_pnl")
    w_total = w_ct * size if (w_ct is not None and size) else None
    fee_total = (row.get("total_fee") or 0) * size if size else None

    warnings = []
    if not size:
        warnings.append("No executable size on at least one leg right now.")
    elif capped:
        warnings.append("Size capped to %s (%d contracts)." % (capped, size))
    elif thin:
        warnings.append(
            "Size capped to the thinner %s book (%d contracts)." % (thin, size))
    if w_ct is not None and w_ct < 0:
        warnings.append(
            "Worst case is NEGATIVE — this is not a guaranteed-positive arb.")
    if row.get("status") != "ARB":
        warnings.append("Scanner status is %s, not ARB." % row.get("status"))

    return {
        "preview_only": True,           # never an executable instruction
        "asset": row.get("asset"),
        "status": row.get("status"),
        "as_of": time.strftime("%H:%M:%S"),
        "size": size,
        "available": _int(avail),
        "thin_leg": thin,
        "guaranteed_positive": (w_ct is not None and w_ct >= 0),
        "buffer_cents": buffer_cents,
        "kalshi": {
            "venue": "Kalshi", "ticker": ticker, "action": "buy",
            "side": k_side, "ask": k_ask, "limit": k_limit,
            "size": size, "cost": k_cost, "title": row.get("kalshi_title"),
        },
        "poly": {
            "venue": "Polymarket", "slug": slug, "action": "buy",
            "side": p_side, "ask": p_ask, "limit": p_limit,
            "size": size, "cost": p_cost, "question": row.get("poly_question"),
        },
        "combined_cost_ct": row.get("combined_cost"),
        "capital_required": capital,
        "worst_pnl_ct": w_ct,
        "worst_pnl_total": w_total,
        "best_pnl_ct": row.get("best_pnl"),
        "fees_total": fee_total,
        "net_return": row.get("net_return"),
        "warnings": warnings,
    }
