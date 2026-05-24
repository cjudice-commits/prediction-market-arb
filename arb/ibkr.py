"""Interactive Brokers Client Portal Gateway client.

The Gateway is a Java app you run locally (`bin/run.sh root/conf.yaml` in the
clientportal.gw distribution). It listens on https://localhost:5000 with a
self-signed cert; we ignore cert verification because we're talking to
localhost. Authentication is interactive (SSO via the user's browser, every
few hours) — the Gateway maintains the session for us; we just call endpoints.

Untestable in this environment until the user installs/auths the Gateway.
Designed to fail loud-but-clear (graceful "not configured" / "not authed")
so the UI can render a setup banner rather than crashing.

Reference: https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/
Event-contract specifics: ForecastEx contracts are modeled as options;
snapshot fields use numeric IDs (31=Last, 84=Bid, 86=Ask, 7295=close, etc.).
"""
import json
import ssl
import urllib.request
import urllib.error

GATEWAY = "https://localhost:5000/v1/api"

# Numeric field IDs for marketdata/snapshot. These are the standard IB codes.
F_LAST = "31"
F_BID = "84"
F_ASK = "86"
F_BID_SZ = "88"
F_ASK_SZ = "85"

# Self-signed cert on localhost: don't verify (we're talking to our own box).
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


class NotConnected(Exception):
    """Gateway isn't running on localhost:5000."""


class NotAuthed(Exception):
    """Gateway is up but the user's SSO session isn't active."""


def _req(method, path, body=None, timeout=8):
    url = GATEWAY + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": "arb-scanner"}
    if data:
        headers["Content-Type"] = "application/json"
    rq = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(rq, timeout=timeout, context=_CTX) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:    # check HTTPError BEFORE URLError —
        if e.code in (401, 403):           # HTTPError is a subclass of URLError
            raise NotAuthed("HTTP %s — sign in at https://localhost:5000"
                            % e.code)
        raise
    except (ConnectionRefusedError, urllib.error.URLError) as e:
        raise NotConnected(str(e))


def auth_status():
    """Returns dict {connected, authenticated, competing, message}."""
    try:
        s = _req("GET", "/iserver/auth/status") or {}
        return {
            "connected": True,
            "authenticated": bool(s.get("authenticated")),
            "competing": bool(s.get("competing")),
            "message": s.get("message"),
        }
    except NotConnected as e:
        return {"connected": False, "authenticated": False, "message": str(e)}
    except NotAuthed as e:
        return {"connected": True, "authenticated": False, "message": str(e)}


def tickle():
    """Pings the Gateway to keep the session alive (~1 hour idle timeout)."""
    try:
        _req("POST", "/tickle", body={})
        return True
    except (NotConnected, NotAuthed, urllib.error.HTTPError):
        return False


def snapshot(conids, fields=None):
    """conids: iterable of int/str. Returns {conid_str: {bid, ask, last, ...}}.
    First call to /marketdata/snapshot often returns partial data; IB warms up
    its market-data subscription. Caller should retry once after ~1s."""
    if not conids:
        return {}
    fields = fields or (F_LAST, F_BID, F_ASK, F_BID_SZ, F_ASK_SZ)
    qs = "conids=%s&fields=%s" % (
        ",".join(str(c) for c in conids), ",".join(fields))
    rows = _req("GET", "/iserver/marketdata/snapshot?" + qs) or []
    out = {}
    def fnum(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    for r in rows:
        cid = str(r.get("conid"))
        out[cid] = {
            "last": fnum(r.get(F_LAST)),
            "bid": fnum(r.get(F_BID)),
            "ask": fnum(r.get(F_ASK)),
            "bid_size": fnum(r.get(F_BID_SZ)),
            "ask_size": fnum(r.get(F_ASK_SZ)),
            "raw": r,
        }
    return out


def search(symbol, sec_type=None):
    """Find conids by symbol (e.g. 'BTC'). Returns list of contract candidates.
    For ForecastEx event contracts, secType may be 'OPT' (modeled as options)
    or 'EVENT' depending on Gateway version — check the result."""
    qs = "symbol=" + symbol
    if sec_type:
        qs += "&secType=" + sec_type
    res = _req("GET", "/iserver/secdef/search?" + qs) or []
    return res
