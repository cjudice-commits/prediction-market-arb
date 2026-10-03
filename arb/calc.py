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
  % Return    AG = pnl / outlay          <- outlay is K+L, fees NOT included
  Annualized  AH = AG * (365 / (close - open))

Not in the sheet: `net_return_cash` = pnl / (outlay + fees). AG's numerator is net
of fees but its denominator isn't, so AG always reads slightly high; the cash
variant divides by what actually leaves the account. Both are emitted — AG keeps
sheet parity and gates ARB status, the cash one is the honest return.

A clean arb pays $1 from exactly one leg only when BOTH contracts ask the same
question (same strike + direction). When strikes differ there is a price band
between the two strikes where the position can double-WIN (free money) or
double-LOSE (basis risk). The sheet surfaces this via AA and AD; we evaluate
all resolution regions and key off the guaranteed worst case, NOT (1 - cost).
"""
from datetime import date

# Kalshi market statuses whose "prices" are NOT real quotes. Only "active" markets
# trade; both POST-close (settled/finalized/closed/determined) and PRE-open
# (initialized/inactive) markets return stale 1.0/0.0 sentinels (yes_ask=0,
# no_ask=1). Treating those as a quote manufactures phantom ~$1.99 "hedges" against
# the sentinel leg (the daily KXBTCD ladder before its trading window opens).
_UNTRADEABLE_KALSHI = {"finalized", "settled", "closed", "determined",
                       "initialized", "inactive"}


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


def _walk_depth(k_ladder, p_ladder, pnl_at, min_edge=0.0):
    """Walk both ask ladders together, taking contracts while the MARGINAL
    guaranteed P&L stays above min_edge.

    Why a greedy walk is exact here: every contract in the bundle shares the
    same worst-case scenario. For a hedged pair both clean regions pay the same
    (1 - cost - fees, whichever leg wins), so the only region that can differ is
    the strike gap — and whether that gap is the worst case is set by strike
    geometry and pairing, NOT by price. The argmin scenario is therefore
    identical for every contract, so min(sum) == sum(min) exactly and the
    bundle's guaranteed P&L decomposes into per-contract marginals with no error
    term. Both ladders ascend in price, so marginal edge falls monotonically:
    the first level that fails the test is the last one worth taking.

    Fees are recomputed at each level's own prices — Kalshi's rate*p*(1-p) is
    price-dependent, so a level deeper is a different fee, not just a worse fill.

    Returns (contracts, gain, kalshi_notional, poly_notional, levels).
    """
    i = j = 0
    k_rem = k_ladder[0][1] if k_ladder else 0.0
    p_rem = p_ladder[0][1] if p_ladder else 0.0
    ct = gain = k_notional = p_notional = 0.0
    levels = []

    while i < len(k_ladder) and j < len(p_ladder):
        k_px, p_px = k_ladder[i][0], p_ladder[j][0]
        marginal = pnl_at(k_px, p_px)
        if marginal <= min_edge:
            break
        q = min(k_rem, p_rem)
        if q <= 0:
            break
        ct += q
        gain += marginal * q
        k_notional += k_px * q
        p_notional += p_px * q
        levels.append({
            "kalshi_price": k_px, "poly_price": p_px,
            "contracts": q, "pnl_per_contract": marginal,
        })
        # Advance whichever side just got eaten (or both, if they tied).
        k_rem -= q
        p_rem -= q
        if k_rem <= 1e-9:
            i += 1
            k_rem = k_ladder[i][1] if i < len(k_ladder) else 0.0
        if p_rem <= 1e-9:
            j += 1
            p_rem = p_ladder[j][1] if j < len(p_ladder) else 0.0

    return ct, gain, k_notional, p_notional, levels


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
        "net_return": None,       # worst_pnl / combined_cost  (sheet parity)
        "annualized": None,
        "net_return_cash": None,  # worst_pnl / (combined_cost + total_fee)
        "annualized_cash": None,
        "cash_cost": None,        # true cash out the door, per contract
        "max_contracts": None, "total_gain": None,
        "tob_contracts": None,     # top-of-book size (instantly fillable)
        "depth_avg_cost": None,    # blended cost across the ladder walk
        "depth_total_fee": None,   # total fees across the sweep
        "depth_net_return": None,  # blended return over the whole sweep
        "depth_net_return_cash": None,
        "depth_annualized": None,
        "depth_levels": [],        # the walk itself, for the detail panel
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
    # Surface which side is missing — bare "NO DATA" hid the real cause
    # (commonly: Kalshi side liquid but the Polymarket slug doesn't resolve).
    if not ks or not ps:
        row["status"] = "NO DATA"
        return row
    if not kq and not pq:
        row["status"] = "NO DATA"
        return row
    if not pq:
        row["status"] = "NO POLY"
        return row
    if not kq or (kq.get("status") in _UNTRADEABLE_KALSHI):
        # Kalshi leg not tradeable (pre-open or settled): prices are 1.0/0.0
        # sentinels, not a real quote — don't build a phantom hedge against them.
        row["status"] = "NO KALSHI"
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
        # Both venues returned live quotes, but the opposite-side asks needed to
        # build a hedge aren't both offered (e.g. deep-OTM 'below' markets where
        # each venue only quotes the cheap YES side). There's data, just no arb.
        row["status"] = "NO ARB"
        return row

    best = None  # (worst_pnl, ...)
    for label, kside, kp, ksz, pside, pp, psz in cands:
        kfee = kfee_rate * kp * (1 - kp)
        # Second-leg fee: Polymarket is proportional (rate * p * (1-p)); IBKR
        # ForecastEx is a FLAT per-contract fee, which the daily adapter passes as
        # pq["flat_fee"]. Honor that override when present.
        _flat = pq.get("flat_fee")
        pfee = _flat if _flat is not None else pfee_rate * pp * (1 - pp)
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

    # True cash-on-cash return. `net_return` above divides by the raw prices to
    # keep workbook parity (AG = pnl/outlay, outlay = K+L), so its numerator is
    # net of fees but its denominator is not — it always reads slightly HIGH.
    # Both venues charge their fee at trade time, so the money that actually
    # leaves the account is price + fee; that's the denominator here, and it's
    # the same basis Positions already uses for open legs (decisions 2026-07-12).
    # Kept ALONGSIDE rather than replacing: net_return gates ARB status and all
    # three alert paths, and re-basing it would break comparability with history.
    cash_cost = cost + total_fee
    net_return_cash = worst / cash_cost if cash_cost else None

    expiry = kq.get("expiry") or pq.get("end_date")
    days = _days_to(expiry, today)
    annualized = (net_return * (365.0 / days)
                  if (net_return is not None and days) else None)
    annualized_cash = (net_return_cash * (365.0 / days)
                       if (net_return_cash is not None and days) else None)

    # --- Depth ------------------------------------------------------------
    # kp/pp/worst/net_return above stay the TOP-OF-BOOK headline: the best rate
    # on offer, comparable across rows and across time. The walk answers a
    # different question — how many contracts are actually executable, and for
    # how many dollars, once you eat back through a thin book.
    k_yes = (kside == "YES")
    p_yes = (pside == "YES")
    _flat_fee = pq.get("flat_fee")

    def _pnl_at(k_px, p_px):
        """Guaranteed P&L for ONE contract filled at exactly these prices."""
        kf = kfee_rate * k_px * (1 - k_px)
        pf = (_flat_fee if _flat_fee is not None
              else pfee_rate * p_px * (1 - p_px))
        return min(_leg(k_px, kf, kt == k_yes) + _leg(p_px, pf, pt == p_yes)
                   for kt, pt in scen)

    k_lad = (kq.get("yes_asks") if k_yes else kq.get("no_asks")) or []
    p_lad = (pq.get("yes_asks") if p_yes else pq.get("no_asks")) or []
    d_ct, d_gain, d_kn, d_pn, d_levels = _walk_depth(
        k_lad, p_lad, _pnl_at, settings.get("depth_min_edge", 0.0))

    # Top-of-book size, kept as its own number so the UI can show how much is
    # available instantly vs. how much needs working back through the book.
    sizes = [s for s in (ksz, psz) if s is not None]
    tob_contracts = min(sizes) if sizes else None

    if k_lad and p_lad and d_ct > 0:
        max_contracts = d_ct
        total_gain = d_gain
        depth_avg_cost = (d_kn + d_pn) / d_ct
        # Blended return over the whole sweep. Fees are re-derived from the
        # levels (each level's own prices) rather than scaled from the
        # top-of-book fee, which would be wrong — the fee is price-dependent.
        d_fee = sum(
            (kfee_rate * lv["kalshi_price"] * (1 - lv["kalshi_price"])
             + (_flat_fee if _flat_fee is not None
                else pfee_rate * lv["poly_price"] * (1 - lv["poly_price"])))
            * lv["contracts"] for lv in d_levels)
        d_notional = d_kn + d_pn
        depth_total_fee = d_fee
        depth_net_return = d_gain / d_notional if d_notional else None
        depth_net_return_cash = (d_gain / (d_notional + d_fee)
                                 if (d_notional + d_fee) else None)
        depth_annualized = (depth_net_return * (365.0 / days)
                            if (depth_net_return is not None and days) else None)
    else:
        # No real ladder yet (phase 1, before the orderbook call) — fall back to
        # the historical top-of-book/open-interest sizing, unchanged.
        max_contracts = tob_contracts
        total_gain = (worst * tob_contracts
                      if tob_contracts is not None else None)
        depth_avg_cost = None
        depth_total_fee = None
        depth_net_return = None
        depth_net_return_cash = None
        depth_annualized = None
        d_levels = []

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
        "net_return_cash": net_return_cash,
        "annualized_cash": annualized_cash,
        "cash_cost": cash_cost,
        "max_contracts": max_contracts, "total_gain": total_gain,
        "tob_contracts": tob_contracts,
        "depth_avg_cost": depth_avg_cost,
        "depth_total_fee": depth_total_fee,
        "depth_net_return": depth_net_return,
        "depth_net_return_cash": depth_net_return_cash,
        "depth_annualized": depth_annualized,
        "depth_levels": d_levels[:12],   # capped: this rides the scan payload
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
