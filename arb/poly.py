"""Polymarket client.

  - Gamma /markets?slug=a&slug=b...  (BATCHED, ~25 slugs/call) -> token ids,
    volume, end date, open/closed flags, image, description.
  - CLOB POST /books                 (1 batched call for ALL tokens) ->
    executable best ask price + size for the YES and NO books separately.
"""
import json
from .net import get_json, post_json, FetchError

_SLUG_CHUNK = 25

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB_BOOKS = "https://clob.polymarket.com/books"


def _loads(s, default):
    if isinstance(s, (list, dict)):
        return s
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def _parse_meta(m):
    slug = m.get("slug")
    outcomes = [str(o).strip().lower() for o in _loads(m.get("outcomes"), [])]
    tokens = _loads(m.get("clobTokenIds"), [])
    yes_id = no_id = None
    for i, oc in enumerate(outcomes):
        if i >= len(tokens):
            break
        if oc == "yes":
            yes_id = str(tokens[i])
        elif oc == "no":
            no_id = str(tokens[i])
    if yes_id is None and len(tokens) >= 1:
        yes_id = str(tokens[0])
    if no_id is None and len(tokens) >= 2:
        no_id = str(tokens[1])
    desc = (m.get("description") or "").strip()
    if len(desc) > 420:
        desc = desc[:419].rstrip() + "…"
    return {
        "slug": slug,
        "question": m.get("question"),
        "description": desc,
        "image": m.get("image") or m.get("icon"),
        "icon": m.get("icon") or m.get("image"),
        "yes_id": yes_id,
        "no_id": no_id,
        "volume": float(m.get("volumeNum") or 0) or 0.0,
        "end_date": (m.get("endDate") or "")[:10] or None,
        "closed": bool(m.get("closed")),
        "active": bool(m.get("active")),
    }


def _best_ask(book):
    """Lowest-price ask level -> (price, size). Order-agnostic."""
    best = None
    for lvl in (book or {}).get("asks") or []:
        try:
            p = float(lvl["price"])
            s = float(lvl["size"])
        except (TypeError, ValueError, KeyError):
            continue
        if best is None or p < best[0]:
            best = (p, s)
    return best


def _best_bid(book):
    """Highest-price bid level -> price (what you could sell into now)."""
    best = None
    for lvl in (book or {}).get("bids") or []:
        try:
            p = float(lvl["price"])
        except (TypeError, ValueError, KeyError):
            continue
        if best is None or p > best:
            best = p
    return best


def fetch_token_bids(token_ids):
    """Batched CLOB books -> {token_id: best_bid_price}. For valuing held
    positions at the executable exit price (not last/mid)."""
    ids = [str(t) for t in dict.fromkeys(token_ids) if t]
    out = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            resp = post_json(CLOB_BOOKS, [{"token_id": t} for t in chunk])
        except FetchError:
            continue
        for b in resp or []:
            out[str(b.get("asset_id"))] = _best_bid(b)
    return out


def _fetch_metas(uniq):
    """Batched Gamma fetch. Returns {slug: parsed_meta} (missing slugs absent)."""
    metas = {}
    for i in range(0, len(uniq), _SLUG_CHUNK):
        chunk = uniq[i:i + _SLUG_CHUNK]
        url = "%s?limit=%d&%s" % (GAMMA, len(chunk) + 5,
                                  "&".join("slug=%s" % s for s in chunk))
        try:
            for m in get_json(url) or []:
                if m.get("slug"):
                    metas[m["slug"]] = _parse_meta(m)
        except FetchError:
            continue
    return metas


def fetch_quotes(slugs):
    """slugs: iterable. Returns {slug: quote|None} with executable YES/NO asks."""
    uniq = sorted({s for s in slugs if s})
    metas = _fetch_metas(uniq)

    token_ids = []
    for v in metas.values():
        for tid in (v["yes_id"], v["no_id"]):
            if tid:
                token_ids.append(tid)

    books = {}
    if token_ids:
        try:
            resp = post_json(CLOB_BOOKS, [{"token_id": t} for t in token_ids])
            for b in resp or []:
                books[str(b.get("asset_id"))] = b
        except FetchError:
            books = {}

    out = {}
    for slug in uniq:
        v = metas.get(slug)
        if v is None:
            out[slug] = None
            continue
        ya = _best_ask(books.get(v["yes_id"] or ""))
        na = _best_ask(books.get(v["no_id"] or ""))
        out[slug] = {
            "slug": slug,
            "question": v["question"],
            "description": v["description"],
            "image": v["image"],
            "icon": v["icon"],
            "yes_ask": ya[0] if ya else None,
            "yes_ask_size": ya[1] if ya else None,
            "no_ask": na[0] if na else None,
            "no_ask_size": na[1] if na else None,
            "volume": v["volume"],
            "end_date": v["end_date"],
            "closed": v["closed"],
        }
    return out
