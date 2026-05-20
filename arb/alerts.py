"""Background arb poller that texts on newly-detected ARBs.

Polls `scan.run_scan` on an interval, tracks the set of currently-ARB pair
ids in memory, and sends an SMS via the configured Apps Script webhook only
when a pair id newly appears. On first cycle we "silently prime" — record
what's currently ARB without firing — so restarting the server does NOT
produce a text storm. A pair that disappears (e.g. price moved) leaves the
set and will re-fire if it returns later.

Config (all required, otherwise the poller no-ops):
  data/secrets.json -> sms_webhook, sms_phone, sms_carrier
"""
import json
import sys
import threading
import time
import urllib.request

from . import positions, scan

POLL_INTERVAL = 60   # seconds between scans
START_DELAY = 5      # let the HTTP server warm up before the first scan
SMS_GAP = 0.5        # space out multiple texts in a single cycle

_state = {"primed": False, "active": set()}


def _log(msg):
    sys.stderr.write("[alerts] %s\n" % msg)
    sys.stderr.flush()


def _send(message, webhook, phone, carrier):
    body = json.dumps({"message": message, "to": phone,
                       "carrier": carrier}).encode()
    req = urllib.request.Request(
        webhook, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = r.status == 200
            if not ok:
                _log("SMS webhook returned %s" % r.status)
            return ok
    except Exception as e:
        _log("SMS send failed: %s: %s" % (type(e).__name__, e))
        return False


def _format(row):
    """SMS-friendly, ASCII, ~80 chars (one segment)."""
    asset = row.get("asset") or "?"
    ks = row.get("kalshi_strike")
    side = (row.get("best_side") or "").replace("YES", "Y").replace("NO", "N")
    net = (row.get("net_return") or 0) * 100
    ann = (row.get("annualized") or 0) * 100
    ct = int(row.get("max_contracts") or 0)
    gain = row.get("total_gain") or 0
    return "ARB %s %s  %s  net %+.2f%%  ann %+.0f%%  %dct  $%.2f" % (
        asset, ks, side, net, ann, ct, gain)


def _pair_id(row):
    return "%s|%s" % (row.get("kalshi_ticker") or "",
                      row.get("poly_slug") or "")


def _cycle():
    secrets = positions.load_secrets()
    webhook = secrets.get("sms_webhook") or ""
    phone = secrets.get("sms_phone") or ""
    carrier = secrets.get("sms_carrier") or ""
    if not (webhook and phone and carrier):
        return                         # SMS not configured -> no-op

    try:
        payload = scan.run_scan(force=False)
    except Exception as e:
        _log("scan failed: %s" % e)
        return

    settings = payload.get("settings", {})
    min_ret = settings.get("min_net_return", 0)

    current = {}
    for r in payload.get("rows", []):
        if r.get("status") == "ARB" and (r.get("net_return") or 0) >= min_ret:
            current[_pair_id(r)] = r

    if not _state["primed"]:
        _state["active"] = set(current)
        _state["primed"] = True
        _log("primed: %d currently-active arb(s), watching for new ones"
             % len(current))
        return

    new = set(current) - _state["active"]
    for pid in new:
        msg = _format(current[pid])
        if _send(msg, webhook, phone, carrier):
            _log("sent: %s" % msg)
        time.sleep(SMS_GAP)
    _state["active"] = set(current)


def _loop():
    time.sleep(START_DELAY)
    while True:
        try:
            _cycle()
        except Exception as e:
            _log("cycle crashed: %s: %s" % (type(e).__name__, e))
        time.sleep(POLL_INTERVAL)


def start_poller():
    """Spawn the daemon thread. Safe to call once at server startup."""
    t = threading.Thread(target=_loop, daemon=True, name="arb-sms-poller")
    t.start()
    _log("poller started (interval=%ds)" % POLL_INTERVAL)
