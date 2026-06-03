#!/usr/bin/env python3
"""Prediction-market arb scanner — pure-stdlib HTTP server.

  python3 server.py [port]        (default 8787)

Routes:
  GET  /                served web UI
  GET  /api/scan        live Kalshi + Polymarket scan (JSON)
  GET  /api/settings    current thresholds
  POST /api/settings    update thresholds (writes data/settings.json)
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from arb import scan, prepare

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")

_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        name, ctype = _STATIC[path]
        try:
            with open(os.path.join(WEB, name), "rb") as f:
                self._send(200, f.read(), ctype)
        except OSError:
            self._send(404, {"error": "not found"})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in _STATIC:
            return self._static(u.path)
        if u.path in ("/api/scan", "/api/scan/hourly", "/api/scan/daily"):
            force = parse_qs(u.query).get("force", ["0"])[0] == "1"
            runner = (scan.run_daily if u.path.endswith("daily")
                      else scan.run_hourly if u.path.endswith("hourly")
                      else scan.run_scan)
            try:
                return self._send(200, runner(force=force))
            except Exception as e:
                return self._send(500, {"error": "%s: %s"
                                        % (type(e).__name__, e)})
        if u.path == "/api/positions":
            force = parse_qs(u.query).get("force", ["0"])[0] == "1"
            try:
                return self._send(200, scan.run_positions(force=force))
            except Exception as e:
                return self._send(500, {"error": "%s: %s"
                                        % (type(e).__name__, e)})
        if u.path == "/api/prepare":
            # Read-only: build a preview ticket for one pair. Places NO orders.
            qs = parse_qs(u.query)
            ticker = qs.get("ticker", [""])[0]
            slug = qs.get("slug", [""])[0]
            raw_max = qs.get("max", [""])[0]
            try:
                max_size = int(float(raw_max)) if raw_max else None
            except (TypeError, ValueError):
                max_size = None
            try:
                buffer_cents = float(qs.get("buffer", ["1"])[0])
            except (TypeError, ValueError):
                buffer_cents = 1.0
            try:
                return self._send(200, prepare.run_prepare(
                    ticker, slug, max_size, buffer_cents))
            except Exception as e:
                return self._send(500, {"error": "%s: %s"
                                        % (type(e).__name__, e)})
        if u.path == "/api/settings":
            return self._send(200, scan.load_settings())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/settings":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            incoming = json.loads(self.rfile.read(n) or b"{}")
            cur = scan.load_settings()
            allowed = ("kalshi_fee_rate", "poly_fee_rate", "min_net_return",
                       "min_poly_volume", "min_contracts")
            for k in allowed:
                if k in incoming:
                    cur[k] = float(incoming[k])
            with open(os.path.join(ROOT, "data", "settings.json"), "w") as f:
                json.dump(cur, f, indent=2)
            scan._CACHE["payload"] = None  # force fresh recompute next scan
            self._send(200, cur)
        except Exception as e:
            self._send(400, {"error": "%s: %s" % (type(e).__name__, e)})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Arb scanner running -> http://127.0.0.1:%d" % port)
    print("Press Ctrl+C to stop.")
    from arb import alerts
    if os.environ.get("ARB_NO_POLLER") == "1":
        print("ARB_NO_POLLER=1 -> local SMS poller off (GitHub Actions alerts).")
    else:
        alerts.start_poller()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        srv.shutdown()


if __name__ == "__main__":
    main()
