#!/usr/bin/env python3
"""
Hyperliquid REST Client — Candle data for live multi-TF polling.

POST https://api.hyperliquid.xyz/info
Body: {"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1m", ...}}

No taker_buy_vol available — delta is always 0.
Limits: 500 candles/request, max 5000 candles history, 1200 weight/min.
"""
import json
import time
import urllib.error
import urllib.request

TIMEFRAMES = ['1m', '3m', '5m', '10m', '15m', '30m', '1h', '2h', '4h', '1d', '1w', '1M']

HL_INFO_URL = 'https://api.hyperliquid.xyz/info'
MAX_PER_REQUEST = 500
MAX_TOTAL = 5000

INTERVAL_MS = {
    '1m': 60_000, '3m': 180_000, '5m': 300_000,
    '10m': 600_000, '15m': 900_000, '30m': 1_800_000,
    '1h': 3_600_000, '2h': 7_200_000, '4h': 14_400_000,
    '8h': 28_800_000, '12h': 43_200_000,
    '1d': 86_400_000, '3d': 259_200_000,
    '1w': 604_800_000, '1M': 2_592_000_000,
}

AGGREGATED_TIMEFRAMES = {
    '10m': ('5m', 2),
}


def _aggregate_ohlcv(candles, target_interval, base_count=None):
    """Aggregate smaller candles into a larger derived interval.

    Base candles are deduplicated by timestamp FIRST (keep first instance):
    pagination overlaps or duplicated rows in a response would otherwise
    double-count volume inside a bucket.

    base_count: when set, only COMPLETE buckets are kept — a bucket must
    contain exactly base_count base candles (e.g. 2 for 10m from 5m).
    Documented strict rule: partial buckets at a window edge (e.g. a 10m
    bucket with only one 5m candle) are DROPPED, never stored. The next
    overlapping load stores their complete version.
    """
    target_ms = INTERVAL_MS[target_interval]
    seen = set()
    unique = []
    for candle in candles:
        ts = int(candle['timestamp'])
        if ts in seen:
            continue
        seen.add(ts)
        unique.append(candle)
    buckets = {}
    order = []
    for candle in unique:
        bucket_ts = (int(candle['timestamp']) // target_ms) * target_ms
        bucket = buckets.get(bucket_ts)
        if bucket is None:
            bucket = {
                'timestamp': bucket_ts,
                'open': candle['open'],
                'high': candle['high'],
                'low': candle['low'],
                'close': candle['close'],
                'volume': float(candle.get('volume') or 0),
                'delta': float(candle.get('delta') or 0),
                '_count': 1,
            }
            for key in (
                'spot_volume', 'spot_delta',
                'futures_volume', 'futures_delta',
                'futures_minus_spot_volume', 'futures_minus_spot_delta',
                'num_trades',
            ):
                if key in candle:
                    bucket[key] = float(candle.get(key) or 0)
            buckets[bucket_ts] = bucket
            order.append(bucket_ts)
            continue

        bucket['high'] = max(bucket['high'], candle['high'])
        bucket['low'] = min(bucket['low'], candle['low'])
        bucket['close'] = candle['close']
        bucket['volume'] += float(candle.get('volume') or 0)
        bucket['delta'] += float(candle.get('delta') or 0)
        bucket['_count'] += 1
        for key in (
            'spot_volume', 'spot_delta',
            'futures_volume', 'futures_delta',
            'futures_minus_spot_volume', 'futures_minus_spot_delta',
            'num_trades',
        ):
            if key in candle:
                bucket[key] = float(bucket.get(key) or 0) + float(candle.get(key) or 0)

    result = [buckets[ts] for ts in sorted(order)]
    if base_count is not None:
        result = [b for b in result if b['_count'] >= base_count]
    for b in result:
        del b['_count']
    return result


def _aggregate_volume_map(volume_map, target_interval):
    """Aggregate timestamp keyed volume maps into a derived interval."""
    if not volume_map:
        return {}
    target_ms = INTERVAL_MS[target_interval]
    result = {}
    for ts in sorted(volume_map.keys()):
        bucket_ts = (int(ts) // target_ms) * target_ms
        src = volume_map[ts]
        bucket = result.setdefault(bucket_ts, {
            'volume': 0.0,
            'volume_usd': 0.0,
            'delta': 0.0,
            'num_trades': 0,
        })
        bucket['volume'] += float(src.get('volume') or 0)
        bucket['volume_usd'] += float(src.get('volume_usd') or 0)
        bucket['delta'] += float(src.get('delta') or 0)
        bucket['num_trades'] += int(src.get('num_trades') or 0)
    return result


def fetch_candles(coin='BTC', interval='1m', start_ms=None, end_ms=None, limit=500):
    """Fetch candles from Hyperliquid info endpoint.

    Returns list of candle dicts: {timestamp, open, high, low, close, volume, delta}
    delta is always 0 (not available from Hyperliquid).
    """
    if interval in AGGREGATED_TIMEFRAMES:
        base_interval, ratio = AGGREGATED_TIMEFRAMES[interval]
        base_limit = min(MAX_TOTAL, max(limit * ratio + ratio, ratio * 20))
        base_candles = fetch_candles(
            coin=coin,
            interval=base_interval,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=base_limit,
        )
        aggregated = _aggregate_ohlcv(base_candles, interval,
                                      base_count=ratio)
        if start_ms is not None:
            aggregated = [c for c in aggregated if c['timestamp'] >= start_ms]
        if end_ms is not None:
            aggregated = [c for c in aggregated if c['timestamp'] < end_ms]
        if len(aggregated) > limit:
            aggregated = aggregated[-limit:]
        return aggregated

    now_ms = int(time.time() * 1000)
    if end_ms is None:
        end_ms = now_ms
    if start_ms is None:
        ims = INTERVAL_MS.get(interval, 60_000)
        start_ms = end_ms - (limit * ims)

    body = json.dumps({
        'type': 'candleSnapshot',
        'req': {
            'coin': coin,
            'interval': interval,
            'startTime': start_ms,
            'endTime': end_ms,
        }
    }).encode()

    req = urllib.request.Request(
        HL_INFO_URL,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'Edgerunner/1.0',
        },
        method='POST',
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read().decode())

    candles = []
    for r in raw:
        candles.append({
            'timestamp': r['t'],
            'open': float(r['o']),
            'high': float(r['h']),
            'low': float(r['l']),
            'close': float(r['c']),
            'volume': float(r['v']),
            'delta': 0,
        })

    candles.sort(key=lambda c: c['timestamp'])
    # Enforce the same strict half-open window [start_ms, end_ms) as the
    # Binance source, so both source paths share one boundary convention.
    candles = [c for c in candles
               if start_ms <= c['timestamp'] < end_ms]
    if len(candles) > limit:
        candles = candles[-limit:]
    return candles


def fetch_candles_paginated(coin='BTC', interval='1m', start_ms=None, end_ms=None):
    """Fetch candles with pagination (500/request, max 5000 total)."""
    now_ms = int(time.time() * 1000)
    if end_ms is None:
        end_ms = now_ms
    if start_ms is None:
        ims = INTERVAL_MS.get(interval, 60_000)
        start_ms = end_ms - (MAX_TOTAL * ims)

    all_candles = []
    current_start = start_ms
    seen = set()

    while current_start < end_ms and len(all_candles) < MAX_TOTAL:
        candles = fetch_candles(coin, interval, current_start, end_ms, MAX_PER_REQUEST)
        if not candles:
            break

        new_count = 0
        for c in candles:
            if c['timestamp'] not in seen:
                seen.add(c['timestamp'])
                all_candles.append(c)
                new_count += 1

        if new_count == 0:
            break

        current_start = candles[-1]['timestamp'] + 1
        time.sleep(0.05)

    all_candles.sort(key=lambda c: c['timestamp'])
    return all_candles[:MAX_TOTAL]


# ── Binance Volume Fetcher ─────────────────────────────────────────────────────
# Binance BTCUSDT Spot klines — real volume + taker buy/sell delta.
# Response per candle: [open_time, o, h, l, c, volume, close_time,
#   quote_vol, num_trades, taker_buy_base_vol, taker_buy_quote_vol, ignore]

BINANCE_KLINES_URL = 'https://api.binance.com/api/v3/klines'
BINANCE_FUTURES_KLINES_URL = 'https://fapi.binance.com/fapi/v1/klines'

# Binance interval strings (same as HL except '1M' → '1M')
BINANCE_INTERVAL = {
    '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
    '1h': '1h', '2h': '2h', '4h': '4h', '8h': '8h', '12h': '12h',
    '1d': '1d', '3d': '3d', '1w': '1w', '1M': '1M',
}


def fetch_binance_volume(interval='1m', start_ms=None, end_ms=None, limit=500):
    """Fetch Binance SPOT BTCUSDT klines and return volume+delta map.

    Returns dict: {timestamp_ms: {'volume': float_btc, 'volume_usd': float,
                                    'delta': float_btc, 'num_trades': int}}
    """
    if interval in AGGREGATED_TIMEFRAMES:
        base_interval, ratio = AGGREGATED_TIMEFRAMES[interval]
        base_limit = min(1500, max(limit * ratio + ratio, ratio * 20))
        base_map = fetch_binance_volume(
            interval=base_interval,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=base_limit,
        )
        aggregated = _aggregate_volume_map(base_map, interval)
        if start_ms is not None:
            aggregated = {ts: row for ts, row in aggregated.items() if ts >= start_ms}
        if end_ms is not None:
            aggregated = {ts: row for ts, row in aggregated.items() if ts < end_ms}
        return dict(sorted(aggregated.items())[-limit:])

    now_ms = int(time.time() * 1000)
    if end_ms is None:
        end_ms = now_ms
    if start_ms is None:
        ims = INTERVAL_MS.get(interval, 60_000)
        start_ms = end_ms - (limit * ims)

    bi = BINANCE_INTERVAL.get(interval)
    if not bi:
        return {}

    params = f'symbol=BTCUSDT&interval={bi}&startTime={start_ms}&endTime={end_ms}&limit={min(limit, 1000)}'
    url = f'{BINANCE_KLINES_URL}?{params}'

    req = urllib.request.Request(url, headers={'User-Agent': 'Edgerunner/1.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read().decode())

    result = {}
    for k in raw:
        ts = int(k[0])
        vol_btc = float(k[5])
        vol_usd = float(k[7])
        taker_buy = float(k[9])
        taker_sell = vol_btc - taker_buy
        delta = taker_buy - taker_sell  # positive = net buying
        result[ts] = {
            'volume': vol_btc,
            'volume_usd': vol_usd,
            'delta': delta,
            'num_trades': int(k[8]),
        }
    return result


def fetch_binance_futures_volume(interval='1m', start_ms=None, end_ms=None, limit=500):
    """Fetch Binance FUTURES BTCUSDT perpetual klines and return volume+delta map.

    Returns dict: {timestamp_ms: {'volume': float_btc, 'volume_usd': float,
                                    'delta': float_btc, 'num_trades': int}}
    """
    if interval in AGGREGATED_TIMEFRAMES:
        base_interval, ratio = AGGREGATED_TIMEFRAMES[interval]
        base_limit = min(1500, max(limit * ratio + ratio, ratio * 20))
        base_map = fetch_binance_futures_volume(
            interval=base_interval,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=base_limit,
        )
        aggregated = _aggregate_volume_map(base_map, interval)
        if start_ms is not None:
            aggregated = {ts: row for ts, row in aggregated.items() if ts >= start_ms}
        if end_ms is not None:
            aggregated = {ts: row for ts, row in aggregated.items() if ts < end_ms}
        return dict(sorted(aggregated.items())[-limit:])

    now_ms = int(time.time() * 1000)
    if end_ms is None:
        end_ms = now_ms
    if start_ms is None:
        ims = INTERVAL_MS.get(interval, 60_000)
        start_ms = end_ms - (limit * ims)

    bi = BINANCE_INTERVAL.get(interval)
    if not bi:
        return {}

    params = f'symbol=BTCUSDT&interval={bi}&startTime={start_ms}&endTime={end_ms}&limit={min(limit, 1500)}'
    url = f'{BINANCE_FUTURES_KLINES_URL}?{params}'

    req = urllib.request.Request(url, headers={'User-Agent': 'Edgerunner/1.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read().decode())

    result = {}
    for k in raw:
        ts = int(k[0])
        vol_btc = float(k[5])
        vol_usd = float(k[7])
        taker_buy = float(k[9])
        taker_sell = vol_btc - taker_buy
        delta = taker_buy - taker_sell
        result[ts] = {
            'volume': vol_btc,
            'volume_usd': vol_usd,
            'delta': delta,
            'num_trades': int(k[8]),
        }
    return result


def _binance_get_json(url, retries=5):
    """GET a Binance endpoint with timeout + backoff on 429/5xx and network errors."""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Edgerunner/1.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 or e.code >= 500:
                # Honor Binance's Retry-After header (seconds) when present,
                # otherwise linear backoff.
                retry_after = e.headers.get('Retry-After') if e.headers else None
                try:
                    delay = float(retry_after) if retry_after is not None \
                        else 1.0 * (attempt + 1)
                except (TypeError, ValueError):
                    delay = 1.0 * (attempt + 1)
                time.sleep(delay)
                continue
            raise
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(
        f'Binance request failed after {retries} attempts: {last_err}')


def fetch_binance_futures_candles(interval='1m', start_ms=None, end_ms=None, limit=1000):
    """Fetch Binance FUTURES BTCUSDT perpetual klines as full candle dicts.

    Same shape as fetch_candles (timestamp = open time ms, OHLC float,
    volume = BTC base volume from k[5]), plus:
      delta           = 2*taker_buy - volume  (real taker delta, k[9])
      futures_volume  = volume   (native futures data, no separate merge needed)
      futures_delta   = delta
      num_trades      = k[8]

    Non-native intervals (in this repo: 10m) are aggregated from the next
    smaller native interval via _aggregate_ohlcv (10m from 5m), paginating
    the base interval internally (Binance caps at 1000 klines/request).
    """
    if interval in AGGREGATED_TIMEFRAMES:
        base_interval, ratio = AGGREGATED_TIMEFRAMES[interval]
        base_ims = INTERVAL_MS[base_interval]
        now_ms = int(time.time() * 1000)
        if end_ms is None:
            end_ms = now_ms
        if start_ms is None:
            start_ms = end_ms - (limit * INTERVAL_MS[interval])

        # Paginate base candles over the full window (max 1000 per request)
        base_candles = []
        seen = set()
        cursor = start_ms
        while cursor < end_ms:
            batch = fetch_binance_futures_candles(
                interval=base_interval, start_ms=cursor, end_ms=end_ms,
                limit=1000)
            new = [c for c in batch if c['timestamp'] not in seen]
            if not new:
                break
            seen.update(c['timestamp'] for c in new)
            base_candles.extend(new)
            cursor = new[-1]['timestamp'] + base_ims
            time.sleep(0.05)

        aggregated = _aggregate_ohlcv(base_candles, interval,
                                      base_count=ratio)
        aggregated = [c for c in aggregated
                      if start_ms <= c['timestamp'] < end_ms]
        return aggregated[-limit:]

    now_ms = int(time.time() * 1000)
    if end_ms is None:
        end_ms = now_ms
    if start_ms is None:
        ims = INTERVAL_MS.get(interval, 60_000)
        start_ms = end_ms - (limit * ims)

    bi = BINANCE_INTERVAL.get(interval)
    if not bi:
        return []

    params = (f'symbol=BTCUSDT&interval={bi}&startTime={start_ms}'
              f'&endTime={end_ms}&limit={min(limit, 1000)}')
    raw = _binance_get_json(f'{BINANCE_FUTURES_KLINES_URL}?{params}')

    candles = []
    for k in raw:
        vol = float(k[5])
        taker_buy = float(k[9])
        delta = 2.0 * taker_buy - vol  # taker_buy - taker_sell
        candles.append({
            'timestamp': int(k[0]),
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': vol,
            'delta': delta,
            'futures_volume': vol,
            'futures_delta': delta,
            'num_trades': int(k[8]),
        })
    candles.sort(key=lambda c: c['timestamp'])
    # Binance endTime is INCLUSIVE — enforce a strict half-open window
    # [start_ms, end_ms) before the limit cut, so the boundary candle is
    # never stored twice across consecutive windows.
    candles = [c for c in candles
               if start_ms <= c['timestamp'] < end_ms]
    return candles[-limit:]


def merge_binance_volume(candles, spot_vol, futures_vol=None):
    """Merge Binance spot/futures volume+delta into Hyperliquid candles (in-place).

    - Keeps legacy `volume`/`delta` as spot-derived values for backward compatibility.
    - Adds explicit fields per candle:
      spot_volume, spot_delta, futures_volume, futures_delta,
      futures_minus_spot_volume, futures_minus_spot_delta.
    """
    futures_vol = futures_vol or {}

    for c in candles:
        ts = c['timestamp']
        sv = spot_vol.get(ts)
        fv = futures_vol.get(ts)

        if sv:
            c['spot_volume'] = sv['volume']
            c['spot_delta'] = sv['delta']
            # legacy compatibility
            c['volume'] = sv['volume']
            c['delta'] = sv['delta']

        if fv:
            c['futures_volume'] = fv['volume']
            c['futures_delta'] = fv['delta']

        spot_v = c.get('spot_volume')
        fut_v = c.get('futures_volume')
        if spot_v is not None and fut_v is not None:
            c['futures_minus_spot_volume'] = fut_v - spot_v

        spot_d = c.get('spot_delta')
        fut_d = c.get('futures_delta')
        if spot_d is not None and fut_d is not None:
            c['futures_minus_spot_delta'] = fut_d - spot_d

    return candles


if __name__ == '__main__':
    print('Testing Hyperliquid API...')
    for tf in ['1m', '3m', '5m', '10m', '15m', '1h', '2h']:
        try:
            candles = fetch_candles('BTC', tf, limit=5)
            if candles:
                c = candles[-1]
                print(f'  {tf:>4s}: {len(candles)} candles, close={c["close"]}, vol={c["volume"]:.2f}')
            else:
                print(f'  {tf:>4s}: no data')
        except Exception as e:
            print(f'  {tf:>4s}: ERROR — {e}')

    print('\nTesting Binance Volume...')
    for tf in ['1m', '3m', '5m', '10m', '1h', '2h']:
        try:
            bv = fetch_binance_volume(tf, limit=3)
            for ts, v in list(bv.items())[-2:]:
                print(f'  {tf:>4s}: ts={ts}  vol={v["volume"]:.4f} BTC  ${v["volume_usd"]:.0f}  delta={v["delta"]:.4f}  trades={v["num_trades"]}')
        except Exception as e:
            print(f'  {tf:>4s}: ERROR — {e}')
