#!/usr/bin/env python3
"""One alert cycle, designed for GitHub Actions cron.

- Runs `scan.run_scan` (public Kalshi + Polymarket APIs; no account creds
  needed for monthly arb discovery).
- Reads/writes `alerts_state.json` at the repo root so successive cron runs
  remember which arbs were already alerted on. The workflow commits this
  file back to the repo (concurrency-guarded).
- On the very first run (no state file yet) we *silently prime* — record
  the currently-active arb set without firing — so you don't get a text
  storm the moment you enable the workflow.
- SMS goes via your existing Apps Script webhook (`SMS_WEBHOOK`), which
  email-relays to your carrier gateway (`SMS_PHONE@SMS_CARRIER`).
- No secrets live on disk in the repo; all SMS values come from GitHub
  Actions Secrets via env vars.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arb import scan          # noqa: E402  (after sys.path)

STATE_FILE = ROOT / "alerts_state.json"


def load_state():
    try:
        return set(json.loads(STATE_FILE.read_text()).get("active", []))
    except (OSError, ValueError):
        return set()


def save_state(active):
    STATE_FILE.write_text(json.dumps({
        "active": sorted(active),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2) + "\n")


def send_sms(message, webhook, phone, carrier):
    body = json.dumps({"message": message, "to": phone,
                       "carrier": carrier}).encode()
    req = urllib.request.Request(
        webhook, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print("send failed: %s: %s" % (type(e).__name__, e))
        return False


def fmt(r):
    asset = r.get("asset") or "?"
    ks = r.get("kalshi_strike")
    side = (r.get("best_side") or "").replace("YES", "Y").replace("NO", "N")
    net = (r.get("net_return") or 0) * 100
    ann = (r.get("annualized") or 0) * 100
    ct = int(r.get("max_contracts") or 0)
    gain = r.get("total_gain") or 0
    return "ARB %s %s  %s  net %+.2f%%  ann %+.0f%%  %dct  $%.2f" % (
        asset, ks, side, net, ann, ct, gain)


def pair_id(r):
    return "%s|%s" % (r.get("kalshi_ticker") or "",
                      r.get("poly_slug") or "")


def main():
    webhook = os.environ.get("SMS_WEBHOOK", "").strip()
    phone = os.environ.get("SMS_PHONE", "").strip()
    carrier = os.environ.get("SMS_CARRIER", "").strip()
    if not (webhook and phone and carrier):
        print("SMS env vars missing — exiting (set SMS_WEBHOOK/SMS_PHONE/SMS_CARRIER)")
        return

    payload = scan.run_scan(force=True)
    settings = payload.get("settings", {})
    min_ret = settings.get("min_net_return", 0)

    current = {pair_id(r): r for r in payload.get("rows", [])
               if r.get("status") == "ARB"
               and (r.get("net_return") or 0) >= min_ret}

    if not STATE_FILE.exists():
        save_state(set(current))
        print("primed (first run): %d active arb(s) recorded, no texts sent"
              % len(current))
        return

    prev = load_state()
    new = sorted(set(current) - prev)
    for pid in new:
        msg = fmt(current[pid])
        if send_sms(msg, webhook, phone, carrier):
            print("sent: %s" % msg)
        time.sleep(0.5)
    save_state(set(current))
    print("cycle: %d active, %d new (sent), %d unchanged"
          % (len(current), len(new), len(current) - len(new)))


if __name__ == "__main__":
    main()
