"""Tiny stdlib HTTP JSON helpers + a bounded thread pool for fan-out fetches."""
import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (prediction-market-arb)",
    "Accept": "application/json",
}


class FetchError(Exception):
    pass


def get_json(url, timeout=20, retries=2):
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = FetchError("HTTP %s for %s" % (e.code, url))
            if e.code not in (429, 500, 502, 503, 504):
                raise last
        except Exception as e:  # URLError, timeout, JSON, etc.
            last = FetchError("%s for %s" % (type(e).__name__, url))
        if attempt < retries:
            time.sleep(0.4 * (attempt + 1))  # linear backoff
    raise last


def post_json(url, payload, timeout=25):
    data = json.dumps(payload).encode()
    headers = dict(_HEADERS)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise FetchError("HTTP %s for %s" % (e.code, url))
    except Exception as e:
        raise FetchError("%s for %s" % (type(e).__name__, url))


def parallel(fn, items, workers=16):
    """Map fn over items concurrently; returns {item: result_or_FetchError}."""
    out = {}
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for fut in futs:
            it = futs[fut]
            try:
                out[it] = fut.result()
            except FetchError as e:
                out[it] = e
            except Exception as e:  # never let one bad fetch kill the scan
                out[it] = FetchError("%s: %s" % (type(e).__name__, e))
    return out
