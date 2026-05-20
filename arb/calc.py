"""Arb math, ported from the workbook formulas.

Authoritative source: the *Arb Positions* sheet (it holds real Excel formulas;
the *Arb Scanner* sheet only holds script-computed values).

  Direction   C  = IF(SEARCH("MINMON",ticker), "Below", "Above")
  Basis %     AC = IF("Above",(kStrike-pStrike)/kStrike,(pStrike-kStrike)/pStrike)
  Kalshi fee  AI = (rate*size*price)*(1-price)   -> per-contract: rate*p*(1-p)
  Poly fee    AJ = size*rate*price*(1-price)     -> per-contract: rate*p*(1-p)
  Per leg     win  -> (1 - price) - fee          (L-AI in the sheet)
              lose -> (- price)   - fee          (-K-AI in the sheet)
  Min Gain    AA = MIN(W+X, Y+Z)   <- the true guaranteed P&L
  In Between  AD = X+Y             <- P&L in the strike-gap region
  % Return    AG = pnl / outlay
  Annualized  AH = AG * (365 / (close - open))

A clean arb pays $1 from exactly one leg only when BOTH contracts ask the same
question (same strike + direction). When strikes differ there is a price band
between the two strikes where the position can double-WIN (free money) or
double-LOSE (basis risk). The sheet surfaces this via AA and AD; we evaluate
all resolution regions and key off the guaranteed worst case, NOT (1 - cost).
"""
from datetime import date


def direction(ticker):
    return "Below" if "MINMON" in (ticker or "").upper() else "Above"


def basis_pct(kalshi_strike, poly_strike, direc):
    if not kalshi_strike or not poly_strike:
        return None
    if direc == "Above":
        return (kalshi_strike - poly_strike) / kalshi_strike
    return (poly_strike - kalshi_strike) / poly_strike


def _days_to(expiry_iso, today=None):
    if not expiry_iso:
        return None
    try:
        y, m, d = (int(x) for x in expiry_iso.split("-")[:3])
        delta = (date(y, m, d) - (today or date.today())).days
        return delta if delta > 0 else None
    except (ValueError, TypeError):
        return None


def _stmt_true(price, strike, is_above):
    return price >= strike if is_above else price <= strike


def _leg(price, fee, won):
    """Per-contract P&L for one leg. Mirrors L-AI / -K-AI in Arb Positions."""
    return ((1.0 - price) - fee) if won else ((-price) - fee)


def _scenarios(ks, ps, is_above):
    """Representative prices covering every resolution region.

    Returns list of (kalshi_stmt_true, poly_stmt_true). The mid region only
    exists when strikes differ; that region is the strike-gap / basis zone.
    """
    lo = min(ks, ps) * 0.5
    hi = max(ks, ps) * 1.5 + 1.0
    pts = [lo, hi]
    if ks != ps:
        pts.append((ks + ps) / 2.0)
    return [(_stmt_true(p, ks, is_above), _stmt_true(p, ps, is_above))
            for p in pts]


def evaluate(pair, kq, pq, settings, today=None):
    """Build one scanner row from a pair + its Kalshi/Poly quotes."""
    tkr = pair["kalshi_ticker"]
    direc = direction(tkr)
    ks = pair.get("kalshi_strike")
    ps = pair.get("poly_strike")
    bpct = basis_pct(ks, ps, direc)

    row = {
        "asset": pair.get("asset"),
        "kalshi_ticker": tkr,
        "kalshi_strike": ks,
        "poly_slug": pair.get("poly_slug"),
        "poly_strike": ps,
        "direction": direc,
        "basis_pct": bpct,
        "basis_favorable": None,
        "best_side": None,
        "kalshi_price": None, "kalshi_size": None,
        "poly_price": None, "poly_size": None,
        "combined_cost": None, "kalshi_fee": None, "poly_fee": None,
        "total_fee": None,
        "worst_pnl": None,        # guaranteed P&L / contract (sheet "Min Gain")
        "best_pnl": None,         # best-case P&L / contract (sheet "Max Gain")
        "mid_pnl": None,          # strike-gap P&L (sheet "In Between")
        "net_return": None,       # worst_pnl / combined_cost
        "annualized": None,
        "max_contracts": None, "total_gain": None,
        "poly_volume": (pq or {}).get("volume"),
        "days_to_expiry": None,
        "status": None,
        # Display metadata straight from the venues' APIs.
        "kalshi_title": (kq or {}).get("title"),
        "kalshi_rules": (kq or {}).get("rules"),
        "kalshi_yes_label": (kq or {}).get("yes_label"),
        "kalshi_no_label": (kq or {}).get("no_label"),
        "poly_question": (pq or {}).get("question"),
        "poly_description": (pq or {}).get("description"),
        "image": (pq or {}).get("image") or (pq or {}).get("icon"),
    }

    if not pair.get("poly_slug"):
        row["status"] = "NO PAIR"
        return row
    if not kq or not pq or not ks or not ps:
        row["status"] = "NO DATA"
        return row

    kfee_rate = settings["kalshi_fee_rate"]
    pfee_rate = settings["poly_fee_rate"]
    is_above = (direc == "Above")
    scen = _scenarios(ks, ps, is_above)

    # Kalshi top-of-book size is only fetched for candidates; until then fall
    # back to open interest as a liquidity proxy for the size gate.
    k_oi = kq.get("open_interest")
    k_ysz = kq.get("yes_ask_size") if kq.get("yes_ask_size") is not None else k_oi
    k_nsz = kq.get("no_ask_size") if kq.get("no_ask_size") is not None else k_oi

    # Candidate hedged pairings: hold opposite sides across the two venues.
    cands = []
    if kq.get("yes_ask") and pq.get("no_ask"):
        cands.append(("YES+NO", "YES", kq["yes_ask"], k_ysz,
                      "NO", pq["no_ask"], pq.get("no_ask_size")))
    if kq.get("no_ask") and pq.get("yes_ask"):
        cands.append(("NO+YES", "NO", kq["no_ask"], k_nsz,
                      "YES", pq["yes_ask"], pq.get("yes_ask_size")))
    if not cands:
        row["status"] = "NO DATA"
        return row

    best = None  # (worst_pnl, ...)
    for label, kside, kp, ksz, pside, pp, psz in cands:
        kfee = kfee_rate * kp * (1 - kp)
        pfee = pfee_rate * pp * (1 - pp)
        k_yes = (kside == "YES")
        p_yes = (pside == "YES")
        pnls = []
        for kt, pt in scen:
            kp_l = _leg(kp, kfee, kt == k_yes)
            pp_l = _leg(pp, pfee, pt == p_yes)
            pnls.append(kp_l + pp_l)
        worst = min(pnls)
        bestc = max(pnls)
        mid = pnls[2] if len(pnls) > 2 else None
        cand = (worst, bestc, mid, label, kside, kp, ksz, pside, pp, psz,
                kfee, pfee)
        if best is None or worst > best[0]:
            best = cand

    (worst, bestc, mid, label, kside, kp, ksz, pside, pp, psz,
     kfee, pfee) = best
    cost = kp + pp
    total_fee = kfee + pfee
    net_return = worst / cost if cost else None

    expiry = kq.get("expiry") or pq.get("end_date")
    days = _days_to(expiry, today)
    annualized = (net_return * (365.0 / days)
                  if (net_return is not None and days) else None)

    sizes = [s for s in (ksz, psz) if s is not None]
    max_contracts = min(sizes) if sizes else None
    total_gain = worst * max_contracts if max_contracts is not None else None

    # Favorable basis == strikes match, or the gap region is not a double-loss.
    fav = (ks == ps) or (mid is not None and mid >= -1e-9)

    row.update({
        "basis_favorable": fav,
        "best_side": label,
        "kalshi_price": kp, "kalshi_size": ksz,
        "poly_price": pp, "poly_size": psz,
        "combined_cost": cost, "kalshi_fee": kfee, "poly_fee": pfee,
        "total_fee": total_fee,
        "worst_pnl": worst, "best_pnl": bestc, "mid_pnl": mid,
        "net_return": net_return, "annualized": annualized,
        "max_contracts": max_contracts, "total_gain": total_gain,
        "days_to_expiry": days,
    })

    # Status precedence mirrors the workbook: DATA > BASIS > RETURN > SIZE.
    min_ret = settings["min_net_return"]
    min_vol = settings["min_poly_volume"]
    min_ct = settings["min_contracts"]
    has_edge = net_return is not None and net_return >= min_ret
    size_ok = (max_contracts is None or max_contracts >= min_ct) \
        and (row["poly_volume"] or 0) >= min_vol

    if not fav and not has_edge:
        row["status"] = "BAD BASIS"   # strike-gap double-loss kills it
    elif not has_edge:
        row["status"] = "NO ARB"      # no guaranteed edge
    elif not size_ok:
        row["status"] = "LOW SIZE"    # real edge, but untradeable size/volume
    else:
        row["status"] = "ARB"
    return row
