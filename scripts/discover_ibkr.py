#!/usr/bin/env python3
"""Discover IBKR ForecastEx YES/NO conid pairs for a given asset + maturity.

Usage:
    python3 scripts/discover_ibkr.py [SYMBOL] [MONTH] [MATURITY_YYYYMMDD]

Defaults: SYMBOL=CFBTC, MONTH=MAY26, MATURITY=20260524

Prints a JSON list of {strike, yes_conid, no_conid, maturity} entries you can
paste into data/ibkr_contracts.json (filling in close_iso + label per row).
Requires the Client Portal Gateway running and authenticated.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from arb.ibkr import _req, auth_status, search


def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "CFBTC"
    month = sys.argv[2] if len(sys.argv) > 2 else "MAY26"
    maturity = sys.argv[3] if len(sys.argv) > 3 else "20260524"

    s = auth_status()
    if not s.get("authenticated"):
        print("Gateway not authenticated. Sign in at https://localhost:5000",
              file=sys.stderr)
        sys.exit(1)

    # 1. find the underlying event conid
    hits = search(sym)
    under = None
    for h in hits:
        for sec in h.get("sections") or []:
            if sec.get("secType") == "EC":          # Event Contract
                under = h.get("conid")
                break
        if under: break
    if not under:
        print("could not find underlying event conid for %s" % sym, file=sys.stderr)
        sys.exit(1)
    print("# underlying: %s conid=%s" % (sym, under), file=sys.stderr)

    # 2. fetch the strike ladder
    strikes = _req("GET", "/iserver/secdef/strikes?conid=%s&sectype=OPT"
                   "&month=%s&exchange=FORECASTX" % (under, month)) or {}
    all_strikes = sorted(set((strikes.get("call") or []) +
                             (strikes.get("put") or [])))
    print("# %d strikes in %s" % (len(all_strikes), month), file=sys.stderr)

    # 3. for each strike, secdef/info gives back the per-side conid; we filter
    #    by maturity to drop other expiries that come back from the same query.
    out = []
    for k in all_strikes:
        try:
            yes = _req("GET", "/iserver/secdef/info?conid=%s&sectype=OPT"
                       "&month=%s&strike=%s&right=C&exchange=FORECASTX"
                       % (under, month, k)) or []
            no = _req("GET", "/iserver/secdef/info?conid=%s&sectype=OPT"
                      "&month=%s&strike=%s&right=P&exchange=FORECASTX"
                      % (under, month, k)) or []
        except Exception as e:
            print("# strike=%s err: %s" % (k, e), file=sys.stderr)
            continue
        yes = yes if isinstance(yes, list) else [yes]
        no = no if isinstance(no, list) else [no]
        ycid = next((x.get("conid") for x in yes
                     if str(x.get("maturityDate") or x.get("maturity_date") or
                            "") == maturity), None)
        ncid = next((x.get("conid") for x in no
                     if str(x.get("maturityDate") or x.get("maturity_date") or
                            "") == maturity), None)
        if ycid and ncid:
            out.append({"strike": k, "yes_conid": ycid, "no_conid": ncid,
                        "maturity": maturity})

    print(json.dumps(out, indent=2))
    print("# wrote %d strike pair(s) for maturity %s" % (len(out), maturity),
          file=sys.stderr)


if __name__ == "__main__":
    main()
