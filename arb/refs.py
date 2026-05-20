"""Reference-price client for the hourly scanner.

Polymarket "Up or Down" resolves on the Binance {SYM}/USDT 1-hour candle
(Up if close >= open). So the *implied strike* for the hour == the Binance
candle OPEN. Kalshi hourly settles on CF Benchmarks (BRTI etc.), whose index
is built from Coinbase/Kraken/Bitstamp/LMAX. The Binance-vs-Coinbase spot gap
is therefore a live proxy for the unhedgeable settlement-feed basis risk.

  data-api.binance.vision  -> Binance public mirror (api.binance.com is 451
                              geo-blocked from here). Gives 1h-candle open +
                              spot for the implied strike & Binance side.
  api.coinbase.com         -> CF-Benchmarks-side spot proxy.
"""
from .net import get_json, parallel, FetchError

BINANCE = "https://data-api.binance.vision/api/v3"
COINBASE = "https://api.coinbase.com/v2/prices/%s-USD/spot"

# asset -> (binance symbol, coinbase code or None if not listed there)
ASSETS = {
    "BTC": ("BTCUSDT", "BTC"),
    "ETH": ("ETHUSDT", "ETH"),
    "SOL": ("SOLUSDT", "SOL"),
    "XRP": ("XRPUSDT", "XRP"),
    "DOGE": ("DOGEUSDT", "DOGE"),
    "BNB": ("BNBUSDT", None),  # not on Coinbase -> no CF-side proxy
}


def _one(asset):
    bsym, cb = ASSETS[asset]
    k = get_json("%s/klines?symbol=%s&interval=1h&limit=1" % (BINANCE, bsym))
    row = k[0]
    open_px = float(row[1])
    open_ms = int(row[0])
    binance_spot = float(get_json(
        "%s/ticker/price?symbol=%s" % (BINANCE, bsym))["price"])
    cf_spot = None
    if cb:
        try:
            cf_spot = float(get_json(COINBASE % cb)["data"]["amount"])
        except (FetchError, KeyError, ValueError, TypeError):
            cf_spot = None
    div = None
    if cf_spot:
        div = (binance_spot - cf_spot) / cf_spot
    return {
        "asset": asset,
        "hour_open": open_px,        # implied Polymarket strike for the hour
        "hour_open_ms": open_ms,     # UTC ms of the candle open (window start)
        "binance_spot": binance_spot,
        "cf_spot": cf_spot,
        "divergence": div,           # (binance - coinbase) / coinbase
    }


def fetch_refs(assets):
    """assets: iterable of symbols. Returns {asset: ref|None}."""
    want = [a for a in assets if a in ASSETS]
    res = parallel(_one, want, workers=8)
    return {a: (None if isinstance(v, FetchError) else v)
            for a, v in res.items()}
