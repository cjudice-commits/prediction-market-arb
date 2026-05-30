#!/usr/bin/env python3
"""Regenerate data/pairs.json for a target settlement month from LIVE markets.

The monthly scanner is driven entirely by data/pairs.json (a curated list of
Kalshi tickers <-> Polymarket slugs). Those identifiers are month-specific, so
when May settles and June lists, the file must be rebuilt or the Monthly tab,
the SMS alerts, and the public dashboard all go stale.

This tool discovers both legs live and rebuilds the file:

  * Kalshi  -> the anchor. For each asset it walks the KX{ASSET}MAXMON /
              MINMON series and keeps every market whose ticker carries the
              target month's expiry code (e.g. 26JUN30). Strike = last ticker
              segment / 100.
  * Poly    -> the coarser ladder. For each asset it reads that month's
              multi-outcome event (forward-parsing each market's slug) and,
              for assets with no event container (HYPE), probes known strikes.
  * Join    -> each Kalshi strike is matched to the NEAREST same-direction
              Polymarket strike (replicating the curated file, which pairs a
              fine Kalshi ladder against a coarse Poly one). Kalshi strikes
              with no Poly market in that direction are emitted Poly-less and
              surface as NO POLY in the scanner.

Usage:
    python3 scripts/roll_month.py --month 2026-06 [--dry-run] [--force]

Safety: refuses to overwrite a populated pairs.json when discovery finds fewer
than --min-pairs markets (default 20) unless --force is given, so running it
before the new month lists can't wipe the working file. The previous file is
copied to pairs.json.bak on every write.
"""
import argparse
import calendar
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAIRS = ROOT / "data" / "pairs.json"
KBASE = "https://api.elections.kalshi.com/trade-api/v2"
GBASE = "https://gamma-api.polymarket.com"

# asset -> Polymarket name + event-slug templates ({m}=month name, {y}=year).
# Assets without an event container (HYPE) carry a `probe` strike list instead.
ASSETS = {
    "BTC":  dict(name="bitcoin",     events=["what-price-will-bitcoin-hit-in-{m}-{y}",
                                             "what-price-will-bitcoin-hit-in-{m}"]),
    "ETH":  dict(name="ethereum",    events=["what-price-will-ethereum-hit-in-{m}-{y}",
                                             "what-price-will-ethereum-hit-in-{m}"]),
    "SOL":  dict(name="solana",      events=["what-price-will-solana-hit-in-{m}-{y}",
                                             "what-price-will-solana-hit-in-{m}"]),
    "XRP":  dict(name="xrp",         events=["what-price-will-xrp-hit-in-{m}-{y}",
                                             "what-price-will-xrp-hit-in-{m}"]),
    "DOGE": dict(name="dogecoin",    events=["what-price-will-dogecoin-hit-in-{m}-{y}",
                                             "what-price-will-dogecoin-hit-in-{m}"]),
    "BNB":  dict(name="bnb",         events=["what-price-will-bnb-hit-in-{m}",
                                             "what-price-will-bnb-hit-in-{m}-{y}"]),
    "HYPE": dict(name="hyperliquid", events=[],
                 probe=[52, 48, 44, 40, 38, 36, 32, 30, 28, 24]),
    "ZEC":  dict(name="zcash",       events=["what-price-will-zcash-hit-in-{m}-{y}",
                                             "what-price-will-zcash-hit-in-{m}"],
                 probe=[]),
}


def gj(url, tries=5):
    """GET JSON with exponential backoff. Kalshi/Poly rate-limit bursts, and a
    silent failure mid-discovery would truncate the ladder, so retry hard."""
    rq = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    delay = 0.5
    for i in range(tries):
        try:
            with urllib.request.urlopen(rq, timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(delay)
            delay = min(delay * 2, 8.0)
    return None


def expiry_code(year, month):
    """2026-06 -> '26JUN30' (Kalshi monthly tickers end on the last day)."""
    last = calendar.monthrange(year, month)[1]
    return "%02d%s%02d" % (year % 100, calendar.month_abbr[month].upper(), last)


def parse_strike_slug(num, k):
    """'85','k' -> 85000 ; '1pt6','' -> 1.6 ; '100','' -> 100."""
    val = float(num.replace("pt", "."))
    return val * 1000.0 if k == "k" else val


# ----------------------------------------------------------------- Kalshi
def kalshi_ladder(asset, expcode):
    """{('above'|'below', strike): ticker} for the target month."""
    out = {}
    for suffix, direc in (("MAXMON", "above"), ("MINMON", "below")):
        series = "KX%s%s" % (asset, suffix)
        cursor = ""
        for _ in range(12):                       # cursor-page guard
            url = "%s/markets?limit=1000&series_ticker=%s" % (KBASE, series)
            if cursor:
                url += "&cursor=" + cursor
            data = gj(url)
            if not data:
                break
            for m in data.get("markets", []):
                tk = m.get("ticker") or ""
                if expcode not in tk:
                    continue
                seg = tk.split("-")[-1]
                if not seg.isdigit():
                    continue
                out[(direc, int(seg) / 100.0)] = tk
            cursor = data.get("cursor") or ""
            if not cursor:
                break
    return out


# ------------------------------------------------------------- Polymarket
def poly_ladder(asset, cfg, month, year):
    """{('above'|'below', strike): slug} discovered for the target month."""
    out = {}
    name = cfg["name"]
    # 1) event container (forward-parse every contained market slug)
    for tmpl in cfg.get("events", []):
        ev = gj("%s/events?slug=%s" % (GBASE,
                tmpl.format(m=month, y=year)))
        if not ev:
            continue
        for mk in ev[0].get("markets", []):
            if mk.get("closed"):
                continue
            sl = mk.get("slug") or ""
            m = re.search(r"-(dip-to|reach)-(\d+(?:pt\d+)?)(k?)-in-%s" % month, sl)
            if not m:
                continue
            direc = "below" if m.group(1) == "dip-to" else "above"
            out[(direc, parse_strike_slug(m.group(2), m.group(3)))] = sl
        if out:
            break                                  # first template that works
    # 2) direct probe for assets with no event (HYPE)
    for strike in cfg.get("probe", []):
        for direc, kw in (("above", "reach"), ("below", "dip-to")):
            if (direc, float(strike)) in out:
                continue
            for variant in ("will-%s-%s-%s-in-%s" % (name, kw, strike, month),
                            "will-%s-%s-%s-in-%s-%s" % (name, kw, strike, month, year)):
                a = gj("%s/markets?slug=%s" % (GBASE, urllib.parse.quote(variant)))
                if a and not a[0].get("closed"):
                    out[(direc, float(strike))] = variant
                    break
    return out


def nearest(poly_for_dir, strike):
    """(poly_strike, slug) closest to `strike`, or (None, None)."""
    best = None
    for ps, slug in poly_for_dir:
        d = abs(ps - strike)
        if best is None or d < best[0]:
            best = (d, ps, slug)
    return (best[1], best[2]) if best else (None, None)


# ------------------------------------------------------------------- main
def build(month_name, year):
    rows, zero_kalshi = [], []
    expcode = expiry_code(year, _MONTHNUM[month_name])
    print("Target expiry code: %s   Poly month: %s-%s" % (expcode, month_name, year))
    for asset, cfg in ASSETS.items():
        kl = kalshi_ladder(asset, expcode)
        if not kl:
            zero_kalshi.append(asset)        # every tracked asset has a ladder;
                                             # empty == a failed/throttled fetch
        pl = poly_ladder(asset, cfg, month_name, year)
        by_dir = {"above": [], "below": []}
        for (d, ps), slug in pl.items():
            by_dir[d].append((ps, slug))
        n_pair = 0
        for (direc, kstrike), tk in sorted(kl.items(),
                                           key=lambda x: (x[0][0], -x[0][1])):
            pstrike, slug = nearest(by_dir[direc], kstrike)
            rows.append({
                "asset": asset,
                "kalshi_ticker": tk,
                "kalshi_strike": kstrike,
                "poly_slug": slug,
                "poly_strike": pstrike,
                "active": True,
            })
            n_pair += slug is not None
        print("  %-5s kalshi=%-3d poly=%-3d  -> %d rows (%d with Poly)"
              % (asset, len(kl), len(pl), len(kl), n_pair))
    return rows, zero_kalshi


_MONTHNUM = {calendar.month_name[i].lower(): i for i in range(1, 13)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="target month YYYY-MM (default: next month)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="write even if fewer than --min-pairs discovered")
    ap.add_argument("--min-pairs", type=int, default=20)
    ap.add_argument("--out", default=str(PAIRS))
    args = ap.parse_args()

    if args.month:
        y, m = (int(x) for x in args.month.split("-")[:2])
    else:
        t = date.today()
        y, m = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
    month_name = calendar.month_name[m].lower()

    rows, zero_kalshi = build(month_name, y)
    with_poly = sum(1 for r in rows if r["poly_slug"])
    print("\nDiscovered %d rows (%d with a Polymarket leg)." % (len(rows), with_poly))

    if zero_kalshi and not args.force:
        print("ABORT: Kalshi discovery returned nothing for %s — almost "
              "certainly a throttled/failed fetch, not reality. Re-run in a "
              "minute (or --force to write a partial file)."
              % ", ".join(zero_kalshi), file=sys.stderr)
        return 2
    if rows and with_poly < 0.3 * len(rows):
        print("WARNING: very few rows got a Polymarket leg (%d/%d). Polymarket "
              "may have changed its %s event-slug pattern — check the per-asset "
              "'poly=' counts above before relying on this file."
              % (with_poly, len(rows), month_name), file=sys.stderr)

    if len(rows) < args.min_pairs and not args.force:
        print("ABORT: only %d rows (< --min-pairs %d). The %s markets may not "
              "be listed yet. Re-run when they are, or pass --force."
              % (len(rows), args.min_pairs, month_name), file=sys.stderr)
        return 2
    if args.dry_run:
        print("--dry-run; not writing.")
        return 0

    out = Path(args.out)
    if out.exists():
        bak = out.with_suffix(".json.bak")
        bak.write_text(out.read_text())
        print("Backed up existing -> %s" % bak)
    out.write_text(json.dumps(rows, indent=2) + "\n")
    print("Wrote %s (%d rows)." % (out, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
