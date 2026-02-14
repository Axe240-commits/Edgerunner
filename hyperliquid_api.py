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
import urllib.request

TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1M']

HL_INFO_URL = 'https://api.hyperliquid.xyz/info'
MAX_PER_REQUEST = 500
MAX_TOTAL = 5000

INTERVAL_MS = {
    '1m': 60_000, '3m': 180_000, '5m': 300_000,
    '15m': 900_000, '30m': 1_800_000,
    '1h': 3_600_000, '2h': 7_200_000, '4h': 14_400_000,
    '8h': 28_800_000, '12h': 43_200_000,
    '1d': 86_400_000, '3d': 259_200_000,
    '1w': 604_800_000, '1M': 2_592_000_000,
}


def fetch_candles(coin='BTC', interval='1m', start_ms=None, end_ms=None, limit=500):
    """Fetch candles from Hyperliquid info endpoint.

    Returns list of candle dicts: {timestamp, open, high, low, close, volume, delta}
    delta is always 0 (not available from Hyperliquid).
    """
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

# Binance interval strings (same as HL except '1M' → '1M')
BINANCE_INTERVAL = {
    '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
    '1h': '1h', '2h': '2h', '4h': '4h', '8h': '8h', '12h': '12h',
    '1d': '1d', '3d': '3d', '1w': '1w', '1M': '1M',
}


def fetch_binance_volume(interval='1m', start_ms=None, end_ms=None, limit=500):
    """Fetch Binance BTCUSDT klines and return volume+delta map.

    Returns dict: {timestamp_ms: {'volume': float_btc, 'volume_usd': float,
                                    'delta': float_btc, 'num_trades': int}}
    """
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


def merge_binance_volume(candles, binance_vol):
    """Merge Binance volume+delta into Hyperliquid candles (in-place).

    Matches by timestamp. Unmatched candles keep original HL values.
    """
    for c in candles:
        bv = binance_vol.get(c['timestamp'])
        if bv:
            c['volume'] = bv['volume']
            c['delta'] = bv['delta']
    return candles


if __name__ == '__main__':
    print('Testing Hyperliquid API...')
    for tf in ['1m', '5m', '15m', '1h']:
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
    for tf in ['1m', '5m', '1h']:
        try:
            bv = fetch_binance_volume(tf, limit=3)
            for ts, v in list(bv.items())[-2:]:
                print(f'  {tf:>4s}: ts={ts}  vol={v["volume"]:.4f} BTC  ${v["volume_usd"]:.0f}  delta={v["delta"]:.4f}  trades={v["num_trades"]}')
        except Exception as e:
            print(f'  {tf:>4s}: ERROR — {e}')
