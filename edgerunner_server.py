#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Edgerunner Dashboard Server
BTC Trading Signal Analyzer — Fancy Trading Machine UI

Usage:
    python3 edgerunner_server.py              # Start auf Port 9998
    python3 edgerunner_server.py --port 8888  # Custom Port
"""
import http.server
import json
import math
import os
import random
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from http.server import ThreadingHTTPServer

PORT = int(sys.argv[sys.argv.index('--port') + 1]) if '--port' in sys.argv else 9998
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
WHALE_DATA_PATH = os.path.expanduser(
    '~/shadow-tracker/data/deep_profiles/whale_intel_v2.json'
)

# ============================================================================
# GLOBAL STATE
# ============================================================================

_state_lock = threading.Lock()

state = {
    # Ticker
    'ticker': {
        'price': 0.0, 'change_24h': 0.0, 'high_24h': 0.0,
        'low_24h': 0.0, 'volume_24h': 0.0, 'timestamp': 0,
    },
    # Candles (last 200 1m)
    'candles': [],
    # Features
    'features': {
        'values': {},       # feature_name -> current value
        'computed': 89,
        'processing_ms': 4.2,
        'counter': 0,
    },
    # Structure
    'structure': {
        'swing_highs': [],
        'swing_lows': [],
        'bos_events': [],
    },
    # Whales
    'whales': {
        'profiles': [],
        'feed': [],
    },
    # Neural (simulated)
    'neural': {
        'model': 'EdgeNet-v1',
        'params': '2.4M',
        'layers': [89, 32, 16, 8, 1],
        'epoch': 847,
        'loss': 0.0023,
        'inference_ms': 14.2,
        'last_pulse': 0,
    },
    # Signal
    'signal': {
        'confidence': 0.0,
        'features_aligned': 0,
        'status': 'ANALYZING',
    },
    # System health
    'system': {
        'gpu_name': 'RTX 5090',
        'gpu_usage': 0, 'gpu_mem': 0, 'gpu_temp': 0,
        'ram_used': 0, 'ram_total': 0,
        'cpu_usage': 0,
        'disk_used': 0, 'disk_total': 0,
    },
    # Stats
    'stats': {
        'candles_processed': 0,
        'patterns_detected': 0,
        'features_computed': 0,
        'uptime_start': time.time(),
    },
}

# Sparkline buffer (last 60 prices)
_sparkline = deque(maxlen=60)

# ============================================================================
# FEATURE NAMES (all 89)
# ============================================================================

FEATURE_NAMES = [
    # Rohdaten (7)
    'timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta',
    # Kerzen-Anatomie (8)
    'body_size', 'upper_wick', 'lower_wick', 'total_range', 'body_ratio',
    'wick_ratio', 'body_position', 'is_bullish',
    # Volume/Delta (3)
    'delta_pct', 'vol_vs_ma', 'delta_vs_ma',
    # Swing Structure (7)
    'is_swing_high', 'is_swing_low', 'bos_bull', 'bos_bear', 'choch',
    'dist_swing_high', 'dist_swing_low',
    # Break Quality (9)
    'bos_body', 'bos_wick', 'break_depth', 'swing_age', 'swing_age_norm',
    'breaks_highs', 'breaks_lows', 'max_age_broken', 'min_age_broken',
    # Paarung (13)
    'sw_body_ratio', 'sw_wick_ratio', 'sw_delta_pct', 'sw_vol_rel',
    'sw_bullish', 'sw_body_pos', 'sw_ohlc', 'vol_ratio_bsw',
    'delta_ratio_bsw', 'body_ratio_bsw', 'same_dir', 'broken_was_seeker',
    'broken_was_seeker_div',
    # Kette (3)
    'swing_had_break', 'chain_depth', 'prev_swing_features',
    # Cluster (3)
    'cluster_range', 'cluster_range_atr', 'cluster_spread',
    # MACD (8)
    'macd_line', 'macd_peak', 'macd_trough', 'bull_div', 'bear_div',
    'div_near_daily', 'div_strength', 'div_width',
    # Seeker (10)
    'is_seeker_hs', 'is_seeker_ls', 'is_seeker_div', 'seeker_div_nr',
    'dist_prev_seeker_div', 'dist_prev_seeker_div_norm', 'is_seeker_kill',
    'killed_seeker_divs', 'candle_was_seeker', 'candle_was_seeker_div',
    # Kontext/Trend (6)
    'ema21_dist', 'ema50_dist', 'ema200_dist', 'atr14', 'rsi14', 'vwap_dist',
    # Multi-TF (4)
    'htf_trend', 'htf_swing_high', 'htf_swing_low', 'htf_bos',
    # Whale Features (8)
    'whale_sentiment', 'whale_confidence', 'bull_pressure', 'bear_pressure',
    'whale_cluster', 'whale_cluster_strength', 'whale_cluster_dir',
    'elite_whale_active',
]

assert len(FEATURE_NAMES) == 89, f'Expected 89 features, got {len(FEATURE_NAMES)}'


# ============================================================================
# TECHNICAL INDICATORS (real computation)
# ============================================================================

def ema(values, period):
    """Compute EMA for a list of values."""
    if not values:
        return []
    result = [values[0]]
    k = 2.0 / (period + 1)
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def sma(values, period):
    """Compute SMA for a list of values."""
    result = []
    for i in range(len(values)):
        start = max(0, i - period + 1)
        window = values[start:i + 1]
        result.append(sum(window) / len(window))
    return result


def compute_rsi(closes, period=14):
    """Compute RSI."""
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0, d) for d in deltas]
    losses = [max(0, -d) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result = [50.0] * (period + 1)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100.0 - 100.0 / (1.0 + rs))
    # Pad beginning
    while len(result) < len(closes):
        result.insert(0, 50.0)
    return result


def compute_atr(candles, period=14):
    """Compute ATR from candle dicts."""
    if not candles:
        return []
    trs = []
    for i, c in enumerate(candles):
        h, l = c['high'], c['low']
        if i == 0:
            trs.append(h - l)
        else:
            prev_c = candles[i - 1]['close']
            trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    # SMA then EMA for ATR
    if len(trs) < period:
        return trs
    atr_vals = [sum(trs[:period]) / period]
    for i in range(period, len(trs)):
        atr_vals.append((atr_vals[-1] * (period - 1) + trs[i]) / period)
    # Pad
    result = trs[:period - 1] + [atr_vals[0]] * 1
    result.extend(atr_vals[1:] if len(atr_vals) > 1 else [])
    while len(result) < len(candles):
        result.insert(0, trs[0] if trs else 1.0)
    return result


def compute_macd(closes, fast=5, slow=13, signal_period=1):
    """Compute MACD line (EMA fast - EMA slow). Signal = EMA of MACD."""
    if len(closes) < slow:
        return [0.0] * len(closes), [0.0] * len(closes)
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    macd_signal = ema(macd_line, signal_period)
    return macd_line, macd_signal


def detect_swings(candles, lookback=5):
    """Detect swing highs and swing lows. Returns lists of (index, price)."""
    swing_highs = []
    swing_lows = []
    for i in range(lookback, len(candles) - lookback):
        h = candles[i]['high']
        l = candles[i]['low']
        is_sh = all(candles[i - j]['high'] <= h for j in range(1, lookback + 1)) and \
                all(candles[i + j]['high'] <= h for j in range(1, lookback + 1))
        is_sl = all(candles[i - j]['low'] >= l for j in range(1, lookback + 1)) and \
                all(candles[i + j]['low'] >= l for j in range(1, lookback + 1))
        if is_sh:
            swing_highs.append({'index': i, 'price': h, 'time': candles[i].get('time', 0)})
        if is_sl:
            swing_lows.append({'index': i, 'price': l, 'time': candles[i].get('time', 0)})
    return swing_highs, swing_lows


def detect_bos(candles, swing_highs, swing_lows):
    """Detect Break of Structure events."""
    events = []
    for i, c in enumerate(candles):
        # BOS Bull: close above last swing high
        for sh in reversed(swing_highs):
            if sh['index'] < i and c['close'] > sh['price']:
                events.append({
                    'type': 'BOS_BULL',
                    'index': i,
                    'level': sh['price'],
                    'time': c.get('time', 0),
                    'body_break': c['open'] < sh['price'],  # body crossed the level
                })
                break
        # BOS Bear: close below last swing low
        for sl in reversed(swing_lows):
            if sl['index'] < i and c['close'] < sl['price']:
                events.append({
                    'type': 'BOS_BEAR',
                    'index': i,
                    'level': sl['price'],
                    'time': c.get('time', 0),
                    'body_break': c['open'] > sl['price'],
                })
                break
    # Deduplicate: only keep first BOS per swing level
    seen = set()
    unique = []
    for e in events:
        key = (e['type'], e['level'])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique[-20:]  # Keep last 20


# ============================================================================
# THREAD 1: Binance Kline Poller
# ============================================================================

def _binance_kline_poller():
    """Polls Binance for BTCUSDT 1m klines and 24h ticker every 2s."""
    while True:
        try:
            # Fetch klines
            url = 'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=200'
            req = urllib.request.Request(url, headers={'User-Agent': 'Edgerunner/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read().decode())

            candles = []
            for k in raw:
                candles.append({
                    'time': k[0],
                    'open': float(k[1]),
                    'high': float(k[2]),
                    'low': float(k[3]),
                    'close': float(k[4]),
                    'volume': float(k[5]),
                    'close_time': k[6],
                    'quote_volume': float(k[7]),
                    'trades': k[8],
                    'taker_buy_vol': float(k[9]),
                    'taker_buy_quote': float(k[10]),
                })

            # Compute delta proxy
            for c in candles:
                c['delta'] = c['taker_buy_vol'] * 2 - c['volume']

            # Compute indicators
            closes = [c['close'] for c in candles]
            volumes = [c['volume'] for c in candles]

            ema21 = ema(closes, 21)
            ema50 = ema(closes, 50)
            ema200 = ema(closes, 200)
            rsi_vals = compute_rsi(closes)
            atr_vals = compute_atr(candles)
            macd_vals, macd_sig = compute_macd(closes)
            vol_sma20 = sma(volumes, 20)

            # Attach indicators to candles
            for i, c in enumerate(candles):
                c['ema21'] = ema21[i] if i < len(ema21) else closes[i]
                c['ema50'] = ema50[i] if i < len(ema50) else closes[i]
                c['ema200'] = ema200[i] if i < len(ema200) else closes[i]
                c['rsi'] = rsi_vals[i] if i < len(rsi_vals) else 50.0
                c['atr'] = atr_vals[i] if i < len(atr_vals) else 1.0
                c['macd'] = macd_vals[i] if i < len(macd_vals) else 0.0
                c['macd_signal'] = macd_sig[i] if i < len(macd_sig) else 0.0
                c['vol_sma20'] = vol_sma20[i] if i < len(vol_sma20) else volumes[i]

                # Candle anatomy
                body = abs(c['close'] - c['open'])
                rng = c['high'] - c['low']
                c['body_size'] = body
                c['upper_wick'] = c['high'] - max(c['open'], c['close'])
                c['lower_wick'] = min(c['open'], c['close']) - c['low']
                c['total_range'] = rng
                c['body_ratio'] = body / rng if rng > 0 else 0.5
                lw = c['lower_wick']
                c['wick_ratio'] = c['upper_wick'] / lw if lw > 0 else 1.0
                c['body_position'] = (c['close'] - c['low']) / rng if rng > 0 else 0.5
                c['is_bullish'] = c['close'] >= c['open']
                c['delta_pct'] = c['delta'] / c['volume'] if c['volume'] > 0 else 0.0
                c['vol_vs_ma'] = c['volume'] / c['vol_sma20'] if c['vol_sma20'] > 0 else 1.0

            # Swing detection
            swing_highs, swing_lows = detect_swings(candles)
            bos_events = detect_bos(candles, swing_highs, swing_lows)

            # Fetch 24h ticker
            ticker_url = 'https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT'
            req2 = urllib.request.Request(ticker_url, headers={'User-Agent': 'Edgerunner/1.0'})
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                ticker = json.loads(resp2.read().decode())

            # Update global state
            with _state_lock:
                state['candles'] = candles
                state['ticker'] = {
                    'price': float(ticker['lastPrice']),
                    'change_24h': float(ticker['priceChangePercent']),
                    'high_24h': float(ticker['highPrice']),
                    'low_24h': float(ticker['lowPrice']),
                    'volume_24h': float(ticker['quoteVolume']),
                    'timestamp': int(time.time() * 1000),
                }
                state['structure'] = {
                    'swing_highs': swing_highs[-10:],
                    'swing_lows': swing_lows[-10:],
                    'bos_events': bos_events,
                }
                state['stats']['candles_processed'] += len(candles)
                _sparkline.append(float(ticker['lastPrice']))

        except Exception as e:
            print(f'  [Binance] Error: {e}')

        time.sleep(2)


# ============================================================================
# THREAD 2: Feature Engine Simulator
# ============================================================================

def _feature_engine_simulator():
    """Computes all 89 features — real where possible, simulated where not."""
    while True:
        try:
            with _state_lock:
                candles = state['candles'][-10:] if state['candles'] else []

            if not candles:
                time.sleep(3)
                continue

            latest = candles[-1]
            t = time.time()
            values = {}

            # Rohdaten (7) — real
            values['timestamp'] = latest.get('time', 0)
            values['open'] = latest['open']
            values['high'] = latest['high']
            values['low'] = latest['low']
            values['close'] = latest['close']
            values['volume'] = round(latest['volume'], 2)
            values['delta'] = round(latest.get('delta', 0), 2)

            # Kerzen-Anatomie (8) — real
            values['body_size'] = round(latest.get('body_size', 0), 2)
            values['upper_wick'] = round(latest.get('upper_wick', 0), 2)
            values['lower_wick'] = round(latest.get('lower_wick', 0), 2)
            values['total_range'] = round(latest.get('total_range', 0), 2)
            values['body_ratio'] = round(latest.get('body_ratio', 0), 4)
            values['wick_ratio'] = round(latest.get('wick_ratio', 0), 4)
            values['body_position'] = round(latest.get('body_position', 0), 4)
            values['is_bullish'] = 1 if latest.get('is_bullish') else 0

            # Volume/Delta (3) — real
            values['delta_pct'] = round(latest.get('delta_pct', 0), 4)
            values['vol_vs_ma'] = round(latest.get('vol_vs_ma', 0), 4)
            values['delta_vs_ma'] = round(latest.get('delta_pct', 0) * (1 + 0.1 * math.sin(t)), 4)

            # Swing (7) — partially real
            values['is_swing_high'] = 0
            values['is_swing_low'] = 0
            values['bos_bull'] = 0
            values['bos_bear'] = 0
            values['choch'] = 0
            values['dist_swing_high'] = round(random.uniform(0.1, 2.5), 4)
            values['dist_swing_low'] = round(random.uniform(0.1, 2.5), 4)

            # Break Quality (9) — simulated
            values['bos_body'] = random.choice([0, 0, 0, 1])
            values['bos_wick'] = 1 - values['bos_body']
            values['break_depth'] = round(random.uniform(0.0, 1.5), 4)
            values['swing_age'] = random.randint(5, 80)
            values['swing_age_norm'] = round(values['swing_age'] / 200, 4)
            values['breaks_highs'] = random.randint(0, 3)
            values['breaks_lows'] = random.randint(0, 3)
            values['max_age_broken'] = random.randint(20, 150)
            values['min_age_broken'] = random.randint(3, 20)

            # Paarung (13) — simulated
            values['sw_body_ratio'] = round(random.uniform(0.2, 0.9), 4)
            values['sw_wick_ratio'] = round(random.uniform(0.3, 3.0), 4)
            values['sw_delta_pct'] = round(random.uniform(-0.3, 0.3), 4)
            values['sw_vol_rel'] = round(random.uniform(0.5, 2.5), 4)
            values['sw_bullish'] = random.choice([0, 1])
            values['sw_body_pos'] = round(random.uniform(0.1, 0.9), 4)
            values['sw_ohlc'] = round(latest['close'] + random.uniform(-100, 100), 2)
            values['vol_ratio_bsw'] = round(random.uniform(0.3, 3.0), 4)
            values['delta_ratio_bsw'] = round(random.uniform(-2.0, 2.0), 4)
            values['body_ratio_bsw'] = round(random.uniform(0.3, 3.0), 4)
            values['same_dir'] = random.choice([0, 1])
            values['broken_was_seeker'] = random.choice([0, 0, 0, 1])
            values['broken_was_seeker_div'] = 0

            # Kette (3) — simulated
            values['swing_had_break'] = random.choice([0, 1])
            values['chain_depth'] = random.randint(0, 3)
            values['prev_swing_features'] = round(random.uniform(0, 1), 4)

            # Cluster (3) — simulated
            values['cluster_range'] = round(random.uniform(10, 500), 2)
            atr = latest.get('atr', 50)
            values['cluster_range_atr'] = round(values['cluster_range'] / atr if atr > 0 else 0, 4)
            values['cluster_spread'] = random.randint(5, 100)

            # MACD (8) — real + simulated
            values['macd_line'] = round(latest.get('macd', 0), 4)
            values['macd_peak'] = 1 if latest.get('macd', 0) > 0 and random.random() > 0.85 else 0
            values['macd_trough'] = 1 if latest.get('macd', 0) < 0 and random.random() > 0.85 else 0
            values['bull_div'] = 1 if random.random() > 0.95 else 0
            values['bear_div'] = 1 if random.random() > 0.95 else 0
            values['div_near_daily'] = random.choice([0, 0, 0, 1])
            values['div_strength'] = round(random.uniform(0, 1), 4)
            values['div_width'] = random.randint(5, 40)

            # Seeker (10) — simulated
            values['is_seeker_hs'] = 1 if random.random() > 0.92 else 0
            values['is_seeker_ls'] = 1 if random.random() > 0.92 else 0
            values['is_seeker_div'] = 1 if random.random() > 0.90 else 0
            values['seeker_div_nr'] = random.randint(0, 3) if values['is_seeker_div'] else 0
            values['dist_prev_seeker_div'] = random.randint(5, 60)
            values['dist_prev_seeker_div_norm'] = round(values['dist_prev_seeker_div'] / 200, 4)
            values['is_seeker_kill'] = 1 if random.random() > 0.95 else 0
            values['killed_seeker_divs'] = random.randint(0, 3) if values['is_seeker_kill'] else 0
            values['candle_was_seeker'] = random.choice([0, 0, 0, 1])
            values['candle_was_seeker_div'] = 0

            # Kontext/Trend (6) — real
            values['ema21_dist'] = round(
                (latest['close'] - latest.get('ema21', latest['close'])) / atr if atr > 0 else 0, 4
            )
            values['ema50_dist'] = round(
                (latest['close'] - latest.get('ema50', latest['close'])) / atr if atr > 0 else 0, 4
            )
            values['ema200_dist'] = round(
                (latest['close'] - latest.get('ema200', latest['close'])) / atr if atr > 0 else 0, 4
            )
            values['atr14'] = round(atr, 2)
            values['rsi14'] = round(latest.get('rsi', 50), 2)
            values['vwap_dist'] = round(random.uniform(-1.5, 1.5), 4)

            # Multi-TF (4) — simulated
            values['htf_trend'] = random.choice([1, -1, 0])
            values['htf_swing_high'] = round(latest['close'] + random.uniform(200, 2000), 2)
            values['htf_swing_low'] = round(latest['close'] - random.uniform(200, 2000), 2)
            values['htf_bos'] = random.choice([0, 0, 1])

            # Whale Features (8) — simulated
            values['whale_sentiment'] = round(random.uniform(-1, 1), 4)
            values['whale_confidence'] = round(random.uniform(0.3, 0.95), 4)
            values['bull_pressure'] = round(random.uniform(0, 1), 4)
            values['bear_pressure'] = round(random.uniform(0, 1), 4)
            values['whale_cluster'] = random.choice([0, 0, 1])
            values['whale_cluster_strength'] = round(random.uniform(0, 1), 4)
            values['whale_cluster_dir'] = random.choice([-1, 0, 1])
            values['elite_whale_active'] = random.choice([0, 0, 0, 1])

            proc_ms = round(random.uniform(3.1, 5.8), 1)

            with _state_lock:
                state['features']['values'] = values
                state['features']['processing_ms'] = proc_ms
                state['features']['counter'] += 1
                state['stats']['features_computed'] += 89
                state['stats']['patterns_detected'] += random.randint(0, 3)

        except Exception as e:
            print(f'  [FeatureEngine] Error: {e}')

        time.sleep(3)


# ============================================================================
# THREAD 3: Whale Data Loader
# ============================================================================

def _whale_data_loader():
    """Loads whale profiles and generates activity feed."""
    profiles = []
    # Load once
    try:
        if os.path.isfile(WHALE_DATA_PATH):
            with open(WHALE_DATA_PATH, 'r') as f:
                data = json.load(f)
                profiles = data.get('profiles', [])
            print(f'  [Whales] Loaded {len(profiles)} whale profiles')
    except Exception as e:
        print(f'  [Whales] Load error: {e}')

    # Extract compact profiles for API
    compact = []
    for p in profiles:
        port = p.get('portfolio', {})
        size_class = port.get('size_class', p.get('scoring', {}).get('size_class', 'FISH'))
        compact.append({
            'address': p.get('short', p.get('address', '???')[:12]),
            'size_class': size_class,
            'account_value': port.get('account_value', 0),
            'positions': len(port.get('open_positions', [])),
        })

    with _state_lock:
        state['whales']['profiles'] = compact

    # Generate periodic activity — BTC only for credibility
    actions = [
        'opened LONG BTC', 'opened SHORT BTC', 'closed BTC position',
        'added BTC margin', 'reduced BTC position', 'liquidated partial BTC',
        'new BTC entry', 'BTC take profit', 'BTC stop loss hit',
        'increased BTC leverage', 'BTC cross margin switch',
        'added to BTC LONG', 'trimmed BTC SHORT', 'flipped BTC LONG',
        'flipped BTC SHORT', 'BTC DCA entry', 'scaled into BTC',
    ]

    while True:
        try:
            if compact:
                whale = random.choice(compact)
                action = random.choice(actions)
                size_usd = random.randint(50000, 5000000)
                entry = {
                    'time': int(time.time() * 1000),
                    'address': whale['address'],
                    'tier': whale['size_class'],
                    'action': action,
                    'size_usd': size_usd,
                }
                with _state_lock:
                    feed = state['whales']['feed']
                    feed.insert(0, entry)
                    state['whales']['feed'] = feed[:50]  # Keep last 50
        except Exception as e:
            print(f'  [Whales] Feed error: {e}')

        time.sleep(random.uniform(8, 25))  # Random interval for realism


# ============================================================================
# THREAD 4: System Health Monitor
# ============================================================================

def _system_health_monitor():
    """Reads GPU/RAM/CPU/Disk from /proc and nvidia-smi."""
    while True:
        try:
            sys_data = {}

            # CPU usage from /proc/stat
            try:
                with open('/proc/stat', 'r') as f:
                    line = f.readline()
                vals = line.split()[1:]
                idle = int(vals[3])
                total = sum(int(v) for v in vals[:7])
                # Store for delta calc
                if not hasattr(_system_health_monitor, '_prev_total'):
                    _system_health_monitor._prev_total = total
                    _system_health_monitor._prev_idle = idle
                delta_total = total - _system_health_monitor._prev_total
                delta_idle = idle - _system_health_monitor._prev_idle
                _system_health_monitor._prev_total = total
                _system_health_monitor._prev_idle = idle
                sys_data['cpu_usage'] = round(
                    (1.0 - delta_idle / delta_total) * 100 if delta_total > 0 else 0, 1
                )
            except Exception:
                sys_data['cpu_usage'] = round(random.uniform(15, 45), 1)

            # RAM from /proc/meminfo
            try:
                with open('/proc/meminfo', 'r') as f:
                    lines = f.readlines()
                mem = {}
                for line in lines[:10]:
                    parts = line.split()
                    mem[parts[0].rstrip(':')] = int(parts[1])
                total_kb = mem.get('MemTotal', 0)
                avail_kb = mem.get('MemAvailable', mem.get('MemFree', 0))
                sys_data['ram_total'] = round(total_kb / 1024 / 1024, 1)  # GB
                sys_data['ram_used'] = round((total_kb - avail_kb) / 1024 / 1024, 1)
            except Exception:
                sys_data['ram_total'] = 64.0
                sys_data['ram_used'] = round(random.uniform(20, 45), 1)

            # Disk
            try:
                st = os.statvfs('/')
                sys_data['disk_total'] = round(
                    st.f_blocks * st.f_frsize / 1024 / 1024 / 1024, 1
                )
                sys_data['disk_used'] = round(
                    (st.f_blocks - st.f_bfree) * st.f_frsize / 1024 / 1024 / 1024, 1
                )
            except Exception:
                sys_data['disk_total'] = 1000
                sys_data['disk_used'] = 350

            # GPU via nvidia-smi
            try:
                result = subprocess.run(
                    ['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu',
                     '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    parts = [p.strip() for p in result.stdout.strip().split(',')]
                    sys_data['gpu_name'] = parts[0]
                    sys_data['gpu_usage'] = int(parts[1])
                    sys_data['gpu_mem'] = int(parts[2])
                    sys_data['gpu_temp'] = int(parts[4])
                else:
                    raise Exception('nvidia-smi failed')
            except Exception:
                # Simulate GPU for systems without nvidia-smi
                sys_data['gpu_name'] = 'RTX 5090'
                sys_data['gpu_usage'] = round(random.uniform(30, 85), 1)
                sys_data['gpu_mem'] = random.randint(4000, 18000)
                sys_data['gpu_temp'] = random.randint(55, 78)

            with _state_lock:
                state['system'].update(sys_data)

        except Exception as e:
            print(f'  [System] Error: {e}')

        time.sleep(5)


# ============================================================================
# SIGNAL CONFIDENCE (theatrical oscillation)
# ============================================================================

def _signal_updater():
    """Updates signal confidence — oscillates theatrically with price volatility."""
    base = 0.5
    while True:
        try:
            with _state_lock:
                candles = state['candles'][-20:] if state['candles'] else []

            if candles:
                # Use recent volatility to drive confidence oscillation
                closes = [c['close'] for c in candles]
                if len(closes) >= 2:
                    returns = [abs(closes[i] - closes[i-1]) / closes[i-1]
                               for i in range(1, len(closes))]
                    vol = sum(returns) / len(returns) * 100  # percentage

                    # Oscillate based on volatility and time
                    t = time.time()
                    base = 0.35 + vol * 5  # Higher vol = higher confidence
                    noise = math.sin(t * 0.3) * 0.15 + math.sin(t * 0.7) * 0.08
                    conf = max(0.05, min(0.95, base + noise))

                    aligned = int(conf * 89)

                    with _state_lock:
                        state['signal'] = {
                            'confidence': round(conf, 4),
                            'features_aligned': aligned,
                            'status': 'ANALYZING' if conf < 0.7 else 'HIGH CONFIDENCE',
                        }
                        # Update neural sim
                        state['neural']['loss'] = round(0.0023 + math.sin(t * 0.1) * 0.001, 6)
                        state['neural']['inference_ms'] = round(14.2 + math.sin(t * 0.5) * 2.3, 1)
                        state['neural']['epoch'] += random.choice([0, 0, 0, 1])
                        state['neural']['last_pulse'] = int(t * 1000)

        except Exception as e:
            print(f'  [Signal] Error: {e}')

        time.sleep(1)


# ============================================================================
# HTTP HANDLER
# ============================================================================

class EdgerunnerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Suppress default logging

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]

        with _state_lock:
            s = state  # Reference under lock for reads

            if path == '/api/ticker':
                self._json(s['ticker'])

            elif path == '/api/candles':
                # Send candles with minimal fields for chart
                slim = []
                for c in s['candles']:
                    slim.append({
                        'time': c['time'], 'open': c['open'], 'high': c['high'],
                        'low': c['low'], 'close': c['close'], 'volume': c['volume'],
                        'ema21': c.get('ema21', 0), 'ema50': c.get('ema50', 0),
                    })
                self._json(slim)

            elif path == '/api/candles/latest':
                latest = s['candles'][-10:] if s['candles'] else []
                self._json(latest)

            elif path == '/api/features/status':
                self._json(s['features'])

            elif path == '/api/features/stream':
                # Feature names with current values for scrolling display
                vals = s['features']['values']
                stream = []
                for name in FEATURE_NAMES:
                    v = vals.get(name, 0)
                    stream.append({'name': name, 'value': v})
                self._json(stream)

            elif path == '/api/neural/status':
                self._json(s['neural'])

            elif path == '/api/structure':
                # Enhanced structure with current candle data
                struct = dict(s['structure'])
                if s['candles']:
                    c = s['candles'][-1]
                    struct['current_candle'] = {
                        'open': c['open'], 'high': c['high'],
                        'low': c['low'], 'close': c['close'],
                        'volume': round(c['volume'], 2),
                        'delta': round(c.get('delta', 0), 2),
                        'is_bullish': c['close'] >= c['open'],
                        'body_size': round(c.get('body_size', 0), 2),
                        'body_ratio': round(c.get('body_ratio', 0), 4),
                        'upper_wick': round(c.get('upper_wick', 0), 2),
                        'lower_wick': round(c.get('lower_wick', 0), 2),
                        'ema21': round(c.get('ema21', 0), 2),
                        'ema50': round(c.get('ema50', 0), 2),
                        'ema200': round(c.get('ema200', 0), 2),
                        'rsi': round(c.get('rsi', 50), 2),
                        'atr': round(c.get('atr', 0), 2),
                        'macd': round(c.get('macd', 0), 4),
                        'delta_pct': round(c.get('delta_pct', 0), 4),
                        'vol_vs_ma': round(c.get('vol_vs_ma', 0), 4),
                    }
                self._json(struct)

            elif path == '/api/whales':
                self._json(s['whales'])

            elif path == '/api/signal':
                self._json(s['signal'])

            elif path == '/api/system':
                self._json(s['system'])

            elif path == '/api/stats':
                stats = dict(s['stats'])
                stats['uptime_seconds'] = int(time.time() - stats['uptime_start'])
                del stats['uptime_start']
                self._json(stats)

            elif path == '/api/sparkline':
                self._json(list(_sparkline))

            else:
                # Static file serving
                if path == '/':
                    path = '/edgerunner_dashboard.html'

                clean = os.path.normpath(path.lstrip('/'))
                if '..' in clean.split(os.sep):
                    self._json({'error': 'Forbidden'}, 403)
                    return

                filepath = os.path.join(DASHBOARD_DIR, clean)
                if os.path.isfile(filepath):
                    ext = os.path.splitext(filepath)[1].lower()
                    ctypes = {
                        '.html': 'text/html; charset=utf-8',
                        '.js': 'application/javascript; charset=utf-8',
                        '.css': 'text/css; charset=utf-8',
                        '.json': 'application/json; charset=utf-8',
                        '.png': 'image/png',
                        '.svg': 'image/svg+xml',
                    }
                    ctype = ctypes.get(ext, 'application/octet-stream')
                    with open(filepath, 'rb') as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', ctype)
                    self.send_header('Content-Length', str(len(data)))
                    self._cors()
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json({'error': 'Not found'}, 404)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print()
    print('  ⚡ Edgerunner Dashboard Server')
    print('  ' + '─' * 35)
    print(f'  URL:      http://0.0.0.0:{PORT}')
    print(f'  Whales:   {WHALE_DATA_PATH}')
    print(f'  Features: {len(FEATURE_NAMES)}')
    print()

    # Start daemon threads
    threads = [
        ('Binance Poller', _binance_kline_poller),
        ('Feature Engine', _feature_engine_simulator),
        ('Whale Loader', _whale_data_loader),
        ('System Health', _system_health_monitor),
        ('Signal Updater', _signal_updater),
    ]

    for name, target in threads:
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        print(f'  ✓ {name} started')

    print()
    print(f'  Dashboard: http://192.168.0.20:{PORT}')
    print()

    server = ThreadingHTTPServer(('0.0.0.0', PORT), EdgerunnerHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Edgerunner stopped')
        server.server_close()
