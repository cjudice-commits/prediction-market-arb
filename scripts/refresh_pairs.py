#!/usr/bin/env python3
"""Refresh data/pairs.json Polymarket slugs against the live Gamma API.

Polymarket periodically appends random hash suffixes to market slugs (e.g.
will-bitcoin-dip-to-70k-in-may-2026 -> ...-2026-438-356-919). When that
happens the scanner silently falls through to NO POLY for the affected
pairs even though both venues have liquid markets.

Usage:
    python3 scripts/refresh_pairs.py [--dry-run]

Walks each asset's monthly multi-outcome event on Gamma, builds a current
(asset, direction, strike) -> slug map, and rewrites pairs.json. Pairs whose
strike has no current Polymarket counterpart are left as-is (the scanner
will surface them as NO POLY so you can decide whether to remove them).
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAIRS = ROOT / "data" / "pairs.json"

ASSET_EVENTS = {
    "BTC":  "what-price-will-bitcoin-hit-in-may-2026",
    "ETH":  "what-price-will-ethereum-hit-in-may-2026",
    "SOL":  "what-price-will-solana-hit-in-may-2026",
    "XRP":  "what-price-will-xrp-hit-in-may-2026",
    "DOGE": "what-price-will-dogecoin-hit-in-may-2026",
    "BNB":  "what-price-will-bnb-hit-in-may",
}
HYPE_STRIKES = [(d, s) for d in ("above", "below")
                for s in (52, 48, 44, 38, 32, 28, 24)]


def gj(u):
    rq = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(rq, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def normalize(asset, raw):
    raw = float(raw)
    return raw * 1000 if asset == "BTC" else raw


def build_remap():
    """Returns {(asset, direction, strike): slug}."""
    out = {}
    for asset, ev_slug in ASSET_EVENTS.items():
        e = gj("https://gamma-api.polymarket.com/events?slug=" + ev_slug)
        if not e:
            print("  WARN: event not found for %s (%s)" % (asset, ev_slug),
                  file=sys.stderr)
            continue
        for mk in e[0].get("markets", []):
            if mk.get("closed"):
                continue
            sl = mk["slug"]
            m = re.search(r"-(dip-to|reach)-(\d+(?:pt\d+)?)k?-in-may", sl)
            if not m:
                continue
            direction = "below" if m.group(1) == "dip-to" else "above"
            strike = normalize(asset, float(m.group(2).replace("pt", ".")))
            out[(asset, direction, strike)] = sl

    # HYPE doesn't have a single-event container; probe known strikes directly.
    for direc, strike in HYPE_STRIKES:
        kw = "reach" if direc == "above" else "dip-to"
        for variant in ("will-hyperliquid-%s-%s-in-may" % (kw, strike),
                        "will-hyperliquid-%s-%s-in-may-2026" % (kw, strike)):
            a = gj("https://gamma-api.polymarket.com/markets?slug=" + variant)
            if a and not a[0].get("closed"):
                out[("HYPE", direc, float(strike))] = variant
                break
    return out


def main():
    dry = "--dry-run" in sys.argv
    remap = build_remap()
    print("Built remap with %d live (asset, direction, strike) entries." % len(remap))

    pairs = json.loads(PAIRS.read_text())
    changed = []
    orphan = []
    for p in pairs:
        if not p.get("kalshi_ticker") or not p.get("poly_strike"):
            continue
        d = "below" if "MIN" in p["kalshi_ticker"] else "above"
        key = (p["asset"], d, float(p["poly_strike"]))
        new = remap.get(key)
        if new is None:
            if p.get("poly_slug"):
                orphan.append((p["asset"], d, p["poly_strike"]))
            continue
        if new != p.get("poly_slug"):
            changed.append((p["asset"], d, p["poly_strike"],
                            p["poly_slug"], new))
            p["poly_slug"] = new

    print("  %d pairs updated, %d pairs have no live Poly counterpart"
          % (len(changed), len(orphan)))
    for asset, d, s, old, new in changed[:8]:
        print("    %s %s $%s  %s  →  %s"
              % (asset, d, s, (old or "")[:40], new[:55]))
    if len(changed) > 8:
        print("    ...(%d more)" % (len(changed) - 8))

    if dry:
        print("  --dry-run; not writing.")
        return
    PAIRS.write_text(json.dumps(pairs, indent=2) + "\n")
    print("  wrote %s (%d pairs total)" % (PAIRS, len(pairs)))


if __name__ == "__main__":
    main()
