#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Edgerunner Dashboard Server — Multi-TF BTC Trading Signal Analyzer

Live data from Hyperliquid (all 9 TFs), history from Binance.
Features computed via CandleAnalyzer, stored in separate DB tables per TF.

Usage:
    python3 edgerunner_server.py              # Start auf Port 9998
    python3 edgerunner_server.py --port 8888  # Custom Port
"""
import http.server
import json
import math
import os
import random
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from http.server import ThreadingHTTPServer

# Edgerunner modules
from candle_analyzer import CandleAnalyzer
from db import (init_db, insert_candles, count_candles, get_candle_by_ts,
                get_ts_range, get_all_tf_stats, DEFAULT_DB_PATH, TIMEFRAMES,
                create_user, authenticate_user, create_session,
                validate_session, delete_session,
                get_settings, save_settings, get_candles_paginated,
                get_candle_neighbors,
                save_pattern, load_pattern, list_patterns, delete_pattern,
                update_pattern_stats, save_signal, get_active_signals,
                dismiss_signal)
import hyperliquid_api

PORT = int(sys.argv[sys.argv.index('--port') + 1]) if '--port' in sys.argv else 9998
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(DASHBOARD_DIR, 'dist')
WHALE_DATA_PATH = os.path.expanduser(
    '~/shadow-tracker/data/deep_profiles/whale_intel_v2.json'
)

# ============================================================================
# GLOBAL STATE
# ============================================================================

_state_lock = threading.RLock()

state = {
    # Active TF for dashboard display
    'active_tf': '1m',
    # Ticker
    'ticker': {
        'price': 0.0, 'change_24h': 0.0, 'high_24h': 0.0,
        'low_24h': 0.0, 'volume_24h': 0.0, 'timestamp': 0,
    },
    # Candles per TF (last 200 each)
    'candles_by_tf': {tf: [] for tf in TIMEFRAMES},
    # Features per TF (latest candle features)
    'features_by_tf': {tf: {} for tf in TIMEFRAMES},
    # Structure per TF
    'structure_by_tf': {tf: {'swing_highs': [], 'swing_lows': [], 'bos_events': []}
                        for tf in TIMEFRAMES},
    # Legacy single-TF references (for backward compat)
    'candles': [],
    'features': {
        'values': {},
        'computed': 89,
        'processing_ms': 0,
        'counter': 0,
    },
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
    'timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta',
    'body_size', 'upper_wick', 'lower_wick', 'total_range', 'body_ratio',
    'wick_ratio', 'body_position', 'is_bullish',
    'delta_pct', 'vol_vs_ma', 'delta_vs_ma',
    'is_swing_high', 'is_swing_low', 'bos_bull', 'bos_bear', 'choch',
    'dist_swing_high', 'dist_swing_low',
    'bos_body', 'bos_wick', 'break_depth', 'swing_age', 'swing_age_norm',
    'breaks_highs', 'breaks_lows', 'max_age_broken', 'min_age_broken',
    'sw_body_ratio', 'sw_wick_ratio', 'sw_delta_pct', 'sw_vol_rel',
    'sw_bullish', 'sw_body_pos', 'sw_ohlc', 'vol_ratio_bsw',
    'delta_ratio_bsw', 'body_ratio_bsw', 'same_dir', 'broken_was_seeker',
    'broken_was_seeker_div',
    'swing_had_break', 'chain_depth', 'prev_swing_features',
    'cluster_range', 'cluster_range_atr', 'cluster_spread',
    'macd_line', 'macd_peak', 'macd_trough', 'bull_div', 'bear_div',
    'div_near_daily', 'div_strength', 'div_width',
    'is_seeker_hs', 'is_seeker_ls', 'is_seeker_div', 'seeker_div_nr',
    'dist_prev_seeker_div', 'dist_prev_seeker_div_norm', 'is_seeker_kill',
    'killed_seeker_divs', 'candle_was_seeker', 'candle_was_seeker_div',
    'ema21_dist', 'ema50_dist', 'ema200_dist', 'atr14', 'rsi14', 'vwap_dist',
    'htf_trend', 'htf_swing_high', 'htf_swing_low', 'htf_bos',
    'whale_sentiment', 'whale_confidence', 'bull_pressure', 'bear_pressure',
    'whale_cluster', 'whale_cluster_strength', 'whale_cluster_dir',
    'elite_whale_active',
]

assert len(FEATURE_NAMES) == 89, f'Expected 89 features, got {len(FEATURE_NAMES)}'

# Swing lookback per TF
TF_LOOKBACK = {
    '1m': 5, '5m': 5, '15m': 3, '30m': 3,
    '1h': 3, '4h': 3, '1d': 2, '1w': 2, '1M': 2,
}

# How many candles to fetch per TF from Hyperliquid
TF_FETCH_LIMIT = {
    '1m': 200, '5m': 200, '15m': 200, '30m': 100,
    '1h': 100, '4h': 100, '1d': 50, '1w': 50, '1M': 30,
}

# ============================================================================
# TECHNICAL INDICATORS (for dashboard display)
# ============================================================================

def ema(values, period):
    if not values:
        return []
    result = [values[0]]
    k = 2.0 / (period + 1)
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def sma(values, period):
    result = []
    for i in range(len(values)):
        start = max(0, i - period + 1)
        window = values[start:i + 1]
        result.append(sum(window) / len(window))
    return result


def compute_rsi(closes, period=14):
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
    while len(result) < len(closes):
        result.insert(0, 50.0)
    return result


def compute_atr(candles, period=14):
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
    if len(trs) < period:
        return trs
    atr_vals = [sum(trs[:period]) / period]
    for i in range(period, len(trs)):
        atr_vals.append((atr_vals[-1] * (period - 1) + trs[i]) / period)
    result = trs[:period - 1] + [atr_vals[0]]
    result.extend(atr_vals[1:] if len(atr_vals) > 1 else [])
    while len(result) < len(candles):
        result.insert(0, trs[0] if trs else 1.0)
    return result


def compute_macd(closes, fast=5, slow=13, signal_period=1):
    if len(closes) < slow:
        return [0.0] * len(closes), [0.0] * len(closes)
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    macd_signal = ema(macd_line, signal_period)
    return macd_line, macd_signal


def detect_swings(candles, lookback=5):
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
    events = []
    for i, c in enumerate(candles):
        for sh in reversed(swing_highs):
            if sh['index'] < i and c['close'] > sh['price']:
                events.append({
                    'type': 'BOS_BULL', 'index': i, 'level': sh['price'],
                    'time': c.get('time', 0), 'body_break': c['open'] < sh['price'],
                })
                break
        for sl in reversed(swing_lows):
            if sl['index'] < i and c['close'] < sl['price']:
                events.append({
                    'type': 'BOS_BEAR', 'index': i, 'level': sl['price'],
                    'time': c.get('time', 0), 'body_break': c['open'] > sl['price'],
                })
                break
    seen = set()
    unique = []
    for e in events:
        key = (e['type'], e['level'])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique[-20:]


# ============================================================================
# THREAD 1: Hyperliquid Multi-TF Poller
# ============================================================================

_db_path = init_db()

# Default analyzer settings
DEFAULT_SETTINGS = {
    'swing_lookback': {tf: TF_LOOKBACK.get(tf, 5) for tf in TIMEFRAMES},
    'macd_fast': 5,
    'macd_slow': 13,
    'macd_signal': 1,
    'rsi_period': 14,
    'atr_period': 14,
    'ema_periods': [21, 50, 200],
    'seeker_min_wick': 0.20,
}

# Load saved settings or use defaults
_current_settings = dict(DEFAULT_SETTINGS)
_saved = get_settings(_db_path)
if _saved:
    _current_settings.update(_saved)


def _build_analyzer(tf):
    """Create a CandleAnalyzer with current settings for a specific TF."""
    lookbacks = _current_settings.get('swing_lookback', {})
    lb = lookbacks.get(tf, TF_LOOKBACK.get(tf, 5)) if isinstance(lookbacks, dict) else 5
    ema_p = _current_settings.get('ema_periods', [21, 50, 200])
    return CandleAnalyzer(
        swing_lookback=lb,
        macd_fast=_current_settings.get('macd_fast', 5),
        macd_slow=_current_settings.get('macd_slow', 13),
        macd_signal=_current_settings.get('macd_signal', 1),
        rsi_period=_current_settings.get('rsi_period', 14),
        atr_period=_current_settings.get('atr_period', 14),
        ema_periods=tuple(ema_p),
        seeker_min_wick=_current_settings.get('seeker_min_wick', 0.20),
    )


def _rebuild_analyzers():
    """Rebuild all analyzers with current settings under lock, force re-analysis."""
    global _analyzers, _last_candle_ts_by_tf
    with _state_lock:
        _analyzers = {tf: _build_analyzer(tf) for tf in TIMEFRAMES}
        _last_candle_ts_by_tf = {tf: 0 for tf in TIMEFRAMES}


_analyzers = {tf: _build_analyzer(tf) for tf in TIMEFRAMES}
_last_candle_ts_by_tf = {tf: 0 for tf in TIMEFRAMES}

# Thread control
THREAD_NAMES = ['HL Multi-TF Poller', 'Ticker Poller', 'Whale Loader',
                'System Health', 'Signal Updater', 'Pattern Detector']
_pause_events = {name: threading.Event() for name in THREAD_NAMES}
for _ev in _pause_events.values():
    _ev.set()  # All running by default

_poll_intervals = {
    'HL Multi-TF Poller': 3,
    'Ticker Poller': 5,
    'Whale Loader': 15,
    'System Health': 5,
    'Signal Updater': 1,
    'Pattern Detector': 10,
}
_enabled_tfs = set(TIMEFRAMES)


def _hyperliquid_poller():
    """Polls ALL timeframes from Hyperliquid, computes features, stores to DB."""
    global _last_candle_ts_by_tf

    while True:
        _pause_events['HL Multi-TF Poller'].wait()
        try:
            for tf in TIMEFRAMES:
                if tf not in _enabled_tfs:
                    continue
                try:
                    limit = TF_FETCH_LIMIT.get(tf, 200)
                    candles = hyperliquid_api.fetch_candles('BTC', tf, limit=limit)

                    if not candles:
                        continue

                    latest_ts = candles[-1]['timestamp']
                    if latest_ts == _last_candle_ts_by_tf[tf]:
                        continue

                    # Merge Binance volume + delta
                    try:
                        bv = hyperliquid_api.fetch_binance_volume(
                            tf,
                            start_ms=candles[0]['timestamp'],
                            end_ms=candles[-1]['timestamp'] + 1,
                            limit=len(candles),
                        )
                        if bv:
                            hyperliquid_api.merge_binance_volume(candles, bv)
                    except Exception as bv_err:
                        pass  # Fall back to HL volume silently

                    # Run feature engine
                    t_start = time.time()
                    features = _analyzers[tf].analyze_batch(candles)
                    proc_ms = round((time.time() - t_start) * 1000, 1)

                    if not features:
                        continue

                    # Store to DB
                    try:
                        insert_candles(features, tf=tf, path=_db_path)
                    except Exception as db_err:
                        print(f'  [HL/{tf}] DB error: {db_err}')

                    # Build display candles (with indicators for dashboard)
                    display_candles = []
                    closes = [c['close'] for c in candles]
                    volumes = [c['volume'] for c in candles]
                    ema21_vals = ema(closes, 21)
                    ema50_vals = ema(closes, 50)

                    for i, c in enumerate(candles):
                        display_candles.append({
                            'time': c['timestamp'],
                            'open': c['open'],
                            'high': c['high'],
                            'low': c['low'],
                            'close': c['close'],
                            'volume': c['volume'],
                            'delta': c.get('delta', 0),
                            'ema21': ema21_vals[i] if i < len(ema21_vals) else c['close'],
                            'ema50': ema50_vals[i] if i < len(ema50_vals) else c['close'],
                        })

                    # Swing/BOS for display
                    lookback = TF_LOOKBACK.get(tf, 5)
                    swing_highs, swing_lows = detect_swings(display_candles, lookback)
                    bos_events = detect_bos(display_candles, swing_highs, swing_lows)

                    # Latest features for API
                    latest_features = features[-1]
                    values = {}
                    for k, v in latest_features.items():
                        if v is None:
                            values[k] = 0
                        else:
                            values[k] = v

                    # Count events in last 10
                    patterns = sum(1 for f in features[-10:] if (
                        f.get('bos_bull') or f.get('bos_bear') or
                        f.get('is_seeker_kill') or f.get('bull_div') or f.get('bear_div')
                    ))

                    # Update state
                    with _state_lock:
                        state['candles_by_tf'][tf] = display_candles
                        state['features_by_tf'][tf] = values
                        state['structure_by_tf'][tf] = {
                            'swing_highs': swing_highs[-10:],
                            'swing_lows': swing_lows[-10:],
                            'bos_events': bos_events,
                        }

                        # Update legacy single-TF references for active TF
                        active = state['active_tf']
                        if tf == active:
                            state['candles'] = display_candles
                            state['features']['values'] = values
                            state['features']['processing_ms'] = proc_ms
                            state['features']['counter'] += 1
                            state['structure'] = state['structure_by_tf'][tf]

                        state['stats']['candles_processed'] += len(candles)
                        state['stats']['features_computed'] += 89
                        state['stats']['patterns_detected'] += patterns

                        # Sparkline from 1m
                        if tf == '1m' and candles:
                            _sparkline.append(candles[-1]['close'])

                    _last_candle_ts_by_tf[tf] = latest_ts

                except Exception as e:
                    print(f'  [HL/{tf}] Error: {e}')
                    continue

        except Exception as e:
            print(f'  [HL Poller] Error: {e}')
            traceback.print_exc()

        time.sleep(_poll_intervals.get('HL Multi-TF Poller', 3))


# ============================================================================
# THREAD 2: Ticker (Binance 24h ticker — lightweight)
# ============================================================================

def _ticker_poller():
    """Polls Binance for 24h ticker data."""
    while True:
        _pause_events['Ticker Poller'].wait()
        try:
            ticker_url = 'https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT'
            req = urllib.request.Request(ticker_url, headers={'User-Agent': 'Edgerunner/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                ticker = json.loads(resp.read().decode())

            with _state_lock:
                state['ticker'] = {
                    'price': float(ticker['lastPrice']),
                    'change_24h': float(ticker['priceChangePercent']),
                    'high_24h': float(ticker['highPrice']),
                    'low_24h': float(ticker['lowPrice']),
                    'volume_24h': float(ticker['quoteVolume']),
                    'timestamp': int(time.time() * 1000),
                }

        except Exception as e:
            print(f'  [Ticker] Error: {e}')

        time.sleep(_poll_intervals.get('Ticker Poller', 5))


# ============================================================================
# THREAD 3: Whale Data Loader
# ============================================================================

def _whale_data_loader():
    """Loads whale profiles and generates activity feed."""
    profiles = []
    try:
        if os.path.isfile(WHALE_DATA_PATH):
            with open(WHALE_DATA_PATH, 'r') as f:
                data = json.load(f)
                profiles = data.get('profiles', [])
            print(f'  [Whales] Loaded {len(profiles)} whale profiles')
    except Exception as e:
        print(f'  [Whales] Load error: {e}')

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

    actions = [
        'opened LONG BTC', 'opened SHORT BTC', 'closed BTC position',
        'added BTC margin', 'reduced BTC position', 'liquidated partial BTC',
        'new BTC entry', 'BTC take profit', 'BTC stop loss hit',
        'increased BTC leverage', 'BTC cross margin switch',
        'added to BTC LONG', 'trimmed BTC SHORT', 'flipped BTC LONG',
        'flipped BTC SHORT', 'BTC DCA entry', 'scaled into BTC',
    ]

    while True:
        _pause_events['Whale Loader'].wait()
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
                    state['whales']['feed'] = feed[:50]
        except Exception as e:
            print(f'  [Whales] Feed error: {e}')

        time.sleep(_poll_intervals.get('Whale Loader', 15))


# ============================================================================
# THREAD 4: System Health Monitor
# ============================================================================

def _system_health_monitor():
    """Reads GPU/RAM/CPU/Disk."""
    while True:
        _pause_events['System Health'].wait()
        try:
            sys_data = {}

            # CPU
            try:
                with open('/proc/stat', 'r') as f:
                    line = f.readline()
                vals = line.split()[1:]
                idle = int(vals[3])
                total = sum(int(v) for v in vals[:7])
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

            # RAM
            try:
                with open('/proc/meminfo', 'r') as f:
                    lines = f.readlines()
                mem = {}
                for line in lines[:10]:
                    parts = line.split()
                    mem[parts[0].rstrip(':')] = int(parts[1])
                total_kb = mem.get('MemTotal', 0)
                avail_kb = mem.get('MemAvailable', mem.get('MemFree', 0))
                sys_data['ram_total'] = round(total_kb / 1024 / 1024, 1)
                sys_data['ram_used'] = round((total_kb - avail_kb) / 1024 / 1024, 1)
            except Exception:
                sys_data['ram_total'] = 64.0
                sys_data['ram_used'] = round(random.uniform(20, 45), 1)

            # Disk
            try:
                st = os.statvfs('/')
                sys_data['disk_total'] = round(st.f_blocks * st.f_frsize / 1024**3, 1)
                sys_data['disk_used'] = round((st.f_blocks - st.f_bfree) * st.f_frsize / 1024**3, 1)
            except Exception:
                sys_data['disk_total'] = 1000
                sys_data['disk_used'] = 350

            # GPU
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
                    raise Exception('no gpu')
            except Exception:
                sys_data['gpu_name'] = 'RTX 5090'
                sys_data['gpu_usage'] = round(random.uniform(30, 85), 1)
                sys_data['gpu_mem'] = random.randint(4000, 18000)
                sys_data['gpu_temp'] = random.randint(55, 78)

            with _state_lock:
                state['system'].update(sys_data)

        except Exception as e:
            print(f'  [System] Error: {e}')

        time.sleep(_poll_intervals.get('System Health', 5))


# ============================================================================
# THREAD 5: Signal Confidence
# ============================================================================

def _signal_updater():
    """Updates signal confidence — oscillates with price volatility."""
    while True:
        _pause_events['Signal Updater'].wait()
        try:
            with _state_lock:
                active = state['active_tf']
                candles = state['candles_by_tf'].get(active, [])[-20:]

            if candles:
                closes = [c['close'] for c in candles]
                if len(closes) >= 2:
                    returns = [abs(closes[i] - closes[i-1]) / closes[i-1]
                               for i in range(1, len(closes))]
                    vol = sum(returns) / len(returns) * 100

                    t = time.time()
                    base = 0.35 + vol * 5
                    noise = math.sin(t * 0.3) * 0.15 + math.sin(t * 0.7) * 0.08
                    conf = max(0.05, min(0.95, base + noise))
                    aligned = int(conf * 89)

                    with _state_lock:
                        state['signal'] = {
                            'confidence': round(conf, 4),
                            'features_aligned': aligned,
                            'status': 'ANALYZING' if conf < 0.7 else 'HIGH CONFIDENCE',
                        }
                        state['neural']['loss'] = round(0.0023 + math.sin(t * 0.1) * 0.001, 6)
                        state['neural']['inference_ms'] = round(14.2 + math.sin(t * 0.5) * 2.3, 1)
                        state['neural']['epoch'] += random.choice([0, 0, 0, 1])
                        state['neural']['last_pulse'] = int(t * 1000)

        except Exception as e:
            print(f'  [Signal] Error: {e}')

        time.sleep(_poll_intervals.get('Signal Updater', 1))


# ============================================================================
# THREAD 6: Pattern Detector
# ============================================================================

_last_detected_ts = {tf: 0 for tf in TIMEFRAMES}
_active_signals = []  # In-memory cache of recent signals

def _pattern_detector():
    """Checks all active saved patterns against new candles."""
    global _active_signals
    while True:
        _pause_events['Pattern Detector'].wait()
        try:
            from live_detector import check_patterns_for_candle, save_detected_signals

            for tf in ['1m', '5m', '15m']:  # Only check fast TFs
                with _state_lock:
                    candles = state['candles_by_tf'].get(tf, [])
                if not candles:
                    continue

                latest_ts = candles[-1].get('time', 0)
                if latest_ts <= _last_detected_ts.get(tf, 0):
                    continue

                signals = check_patterns_for_candle(latest_ts, tf, path=_db_path)
                if signals:
                    saved = save_detected_signals(signals, path=_db_path)
                    if saved > 0:
                        # Refresh in-memory cache
                        from db import get_active_signals
                        _active_signals = get_active_signals(hours=24, path=_db_path)
                        print(f'  [PatternDetector] {saved} new signal(s) on {tf}')

                _last_detected_ts[tf] = latest_ts

        except Exception as e:
            print(f'  [PatternDetector] Error: {e}')

        time.sleep(_poll_intervals.get('Pattern Detector', 10))


# ============================================================================
# AI PRE-ANALYSE — Strukturierte Kerzen-Zusammenfassung
# ============================================================================

def _build_candle_analysis(candle_data, lookback_count):
    """Baut eine vor-interpretierte Analyse-Zusammenfassung fuer den AI Chat.

    Statt 80+ rohe Zahlen: klare Fakten-Saetze die das LLM nur noch
    erklaeren/kommentieren muss, nicht interpretieren.
    """
    if not candle_data or not candle_data.get('meta'):
        return None

    m = candle_data['meta']
    lines = []

    # --- 1. Kerzen-Steckbrief ---
    ts_val = m.get('timestamp', 0)
    ts_str = datetime.fromtimestamp(ts_val / 1000).strftime('%Y-%m-%d %H:%M:%S')
    is_bull = bool(m.get('is_bullish'))
    direction = 'BULLISH (gruene Kerze)' if is_bull else 'BEARISH (rote Kerze)'

    lines.append(f'KERZE: {ts_str}')
    lines.append(f'Richtung: {direction}')
    lines.append(f'Open: {m.get("open", 0):.2f} | High: {m.get("high", 0):.2f} | Low: {m.get("low", 0):.2f} | Close: {m.get("close", 0):.2f}')

    body = abs((m.get('close', 0) or 0) - (m.get('open', 0) or 0))
    total_range = m.get('total_range', 0) or 0
    lines.append(f'Body: {body:.2f} | Range: {total_range:.2f} | Body-Ratio: {(m.get("body_ratio", 0) or 0):.2f}')
    lines.append(f'Volume: {(m.get("volume", 0) or 0):.2f} | Delta: {(m.get("delta", 0) or 0):.2f}')
    lines.append('')

    # --- 2. Struktur-Events (nur aktive) ---
    events = []
    if m.get('bos_bull'):
        events.append('BOS BULL (Bullish Break of Structure) — Preis hat letztes Swing High gebrochen')
    if m.get('bos_bear'):
        events.append('BOS BEAR (Bearish Break of Structure) — Preis hat letztes Swing Low gebrochen')
    if m.get('choch'):
        events.append('CHoCH (Change of Character) — Trendwechsel-Signal')
    if m.get('is_swing_high'):
        events.append('SWING HIGH — Diese Kerze ist ein lokales Hoch')
    if m.get('is_swing_low'):
        events.append('SWING LOW — Diese Kerze ist ein lokales Tief')
    if m.get('bull_div'):
        ds = m.get('div_strength', 0) or 0
        events.append(f'BULLISCHE DIVERGENZ (Staerke: {ds:.4f}) — Preis faellt aber MACD steigt')
    if m.get('bear_div'):
        ds = m.get('div_strength', 0) or 0
        events.append(f'BAERISCHE DIVERGENZ (Staerke: {ds:.4f}) — Preis steigt aber MACD faellt')
    if m.get('is_seeker_kill'):
        events.append(f'SEEKER KILL — {m.get("killed_seeker_divs", 0)} Seeker-Div(s) invalidiert')
    if m.get('is_seeker_div'):
        events.append(f'SEEKER DIVERGENZ #{m.get("seeker_div_nr", 0)} aktiv')

    if events:
        lines.append('STRUKTUR-EVENTS:')
        for e in events:
            lines.append(f'  * {e}')
    else:
        lines.append('STRUKTUR-EVENTS: Keine (neutrale Kerze ohne BOS/CHoCH/Divergenz)')
    lines.append('')

    # --- 3. Indikatoren (mit Interpretation) ---
    rsi = m.get('rsi14', 0) or 0
    if rsi < 30:
        rsi_text = f'RSI: {rsi:.2f} — UEBERVERKAUFT'
    elif rsi > 70:
        rsi_text = f'RSI: {rsi:.2f} — UEBERKAUFT'
    elif rsi < 40:
        rsi_text = f'RSI: {rsi:.2f} — niedrig (nahe ueberverkauft)'
    elif rsi > 60:
        rsi_text = f'RSI: {rsi:.2f} — hoch (nahe ueberkauft)'
    else:
        rsi_text = f'RSI: {rsi:.2f} — neutral'

    vol = m.get('vol_vs_ma', 0) or 0
    if vol > 2.0:
        vol_text = f'Volume vs MA: {vol:.2f} — SEHR HOCH (>2x Durchschnitt)'
    elif vol > 1.5:
        vol_text = f'Volume vs MA: {vol:.2f} — HOCH (>1.5x Durchschnitt)'
    elif vol < 0.5:
        vol_text = f'Volume vs MA: {vol:.2f} — NIEDRIG (<0.5x Durchschnitt)'
    else:
        vol_text = f'Volume vs MA: {vol:.2f} — normal'

    macd = m.get('macd_line', 0) or 0
    macd_text = f'MACD: {macd:.4f}'
    if m.get('macd_peak'):
        macd_text += ' (PEAK — moegliche Umkehr nach unten)'
    elif m.get('macd_trough'):
        macd_text += ' (TROUGH — moegliche Umkehr nach oben)'

    delta_pct = m.get('delta_pct', 0) or 0
    if delta_pct > 50:
        delta_text = f'Delta %: {delta_pct:.2f}% — starker Kaufdruck'
    elif delta_pct < -50:
        delta_text = f'Delta %: {delta_pct:.2f}% — starker Verkaufsdruck'
    else:
        delta_text = f'Delta %: {delta_pct:.2f}%'

    lines.append('INDIKATOREN:')
    lines.append(f'  {rsi_text}')
    lines.append(f'  {vol_text}')
    lines.append(f'  {macd_text}')
    lines.append(f'  {delta_text}')
    lines.append(f'  ATR(14): {(m.get("atr14", 0) or 0):.2f}')
    lines.append('')

    # --- 4. Trend-Kontext (EMAs) ---
    ema21 = m.get('ema21_dist', 0) or 0
    ema50 = m.get('ema50_dist', 0) or 0
    ema200 = m.get('ema200_dist', 0) or 0
    vwap = m.get('vwap_dist', 0) or 0

    lines.append('TREND-KONTEXT:')
    lines.append(f'  EMA21 Abstand: {ema21:.4f}% ({"drueber" if ema21 > 0 else "drunter"})')
    lines.append(f'  EMA50 Abstand: {ema50:.4f}% ({"drueber" if ema50 > 0 else "drunter"})')
    lines.append(f'  EMA200 Abstand: {ema200:.4f}% ({"drueber" if ema200 > 0 else "drunter"})')
    lines.append(f'  VWAP Abstand: {vwap:.4f}%')

    htf = m.get('htf_trend', 0) or 0
    if htf > 0:
        lines.append(f'  HTF Trend: {htf} (hoehere TF ist BULLISH)')
    elif htf < 0:
        lines.append(f'  HTF Trend: {htf} (hoehere TF ist BEARISH)')
    else:
        lines.append(f'  HTF Trend: {htf} (neutral)')
    lines.append('')

    # --- 5. Break-Qualitaet (nur wenn BOS aktiv) ---
    if m.get('bos_bull') or m.get('bos_bear'):
        bd = m.get('break_depth', 0) or 0
        sa = m.get('swing_age', 0) or 0
        san = m.get('swing_age_norm', 0) or 0
        lines.append('BREAK-QUALITAET:')
        lines.append(f'  Break-Tiefe: {bd:.4f}')
        lines.append(f'  Swing-Alter: {sa} Kerzen (normalisiert: {san:.2f})')
        lines.append(f'  Body-Break: {"JA" if m.get("bos_body") else "NEIN"} | Wick-Break: {"JA" if m.get("bos_wick") else "NEIN"}')
        lines.append(f'  Breaks Highs: {m.get("breaks_highs", 0)} | Breaks Lows: {m.get("breaks_lows", 0)}')
        if m.get('broken_was_seeker'):
            lines.append(f'  Gebrochener Swing war SEEKER')
        if m.get('broken_was_seeker_div'):
            lines.append(f'  Gebrochener Swing war SEEKER-DIV')
        lines.append('')

    # --- 6. Whale-Daten (nur wenn relevant) ---
    ws = m.get('whale_sentiment', 0) or 0
    wc = m.get('whale_confidence', 0) or 0
    bp = m.get('bull_pressure', 0) or 0
    brp = m.get('bear_pressure', 0) or 0
    if ws != 0 or wc > 0 or bp > 0 or brp > 0:
        lines.append('WHALE-DATEN:')
        if ws > 0:
            lines.append(f'  Sentiment: {ws:.4f} (BULLISH)')
        elif ws < 0:
            lines.append(f'  Sentiment: {ws:.4f} (BEARISH)')
        else:
            lines.append(f'  Sentiment: {ws:.4f} (neutral)')
        lines.append(f'  Konfidenz: {wc:.4f}')
        lines.append(f'  Bull Pressure: {bp:.4f} | Bear Pressure: {brp:.4f}')
        if m.get('elite_whale_active'):
            lines.append(f'  ELITE WHALE AKTIV (>$1M)')
        if m.get('whale_cluster'):
            wcs = m.get('whale_cluster_strength', 0) or 0
            wcd = m.get('whale_cluster_dir', 0) or 0
            lines.append(f'  Whale-Cluster: Staerke {wcs:.4f}, Richtung {wcd:.4f}')
        lines.append('')

    # --- 7. Lookback-Kerzen Zusammenfassung ---
    if candle_data.get('before'):
        before = candle_data['before']
        pattern_candles = before[-lookback_count:] if len(before) >= lookback_count else before
        if pattern_candles:
            lines.append('VORHERIGE KERZEN:')
            for i, c in enumerate(pattern_candles):
                offset = -(len(pattern_candles) - i)
                cts = datetime.fromtimestamp((c.get('timestamp', 0) or 0) / 1000).strftime('%H:%M')
                c_dir = 'BULL' if c.get('is_bullish') else 'BEAR'
                c_rsi = c.get('rsi14', 0) or 0
                c_vol = c.get('vol_vs_ma', 0) or 0
                c_close = c.get('close', 0) or 0
                c_events = []
                if c.get('bos_bull'): c_events.append('BOS+')
                if c.get('bos_bear'): c_events.append('BOS-')
                if c.get('choch'): c_events.append('CHoCH')
                if c.get('bull_div'): c_events.append('DIV+')
                if c.get('bear_div'): c_events.append('DIV-')
                evt_str = f' [{",".join(c_events)}]' if c_events else ''
                lines.append(
                    f'  [{offset}] {cts} {c_dir} Close:{c_close:.2f} RSI:{c_rsi:.1f} Vol:{c_vol:.2f}{evt_str}'
                )
            lines.append('')

    # --- 8. Gesamt-Einschaetzung ---
    bull_signals = []
    bear_signals = []
    if is_bull: bull_signals.append('Gruene Kerze')
    else: bear_signals.append('Rote Kerze')
    if m.get('bos_bull'): bull_signals.append('BOS Bull')
    if m.get('bos_bear'): bear_signals.append('BOS Bear')
    if m.get('bull_div'): bull_signals.append('Bull Divergenz')
    if m.get('bear_div'): bear_signals.append('Bear Divergenz')
    if rsi < 30: bull_signals.append('RSI ueberverkauft')
    if rsi > 70: bear_signals.append('RSI ueberkauft')
    if vol > 1.5: bull_signals.append('Hohes Volume')
    if ws > 0: bull_signals.append('Whale bullish')
    if ws < 0: bear_signals.append('Whale bearish')
    if ema21 > 0: bull_signals.append('Ueber EMA21')
    if ema21 < 0: bear_signals.append('Unter EMA21')

    lines.append('ZUSAMMENFASSUNG:')
    lines.append(f'  Bullish-Signale ({len(bull_signals)}): {", ".join(bull_signals) if bull_signals else "keine"}')
    lines.append(f'  Bearish-Signale ({len(bear_signals)}): {", ".join(bear_signals) if bear_signals else "keine"}')
    if len(bull_signals) > len(bear_signals):
        lines.append(f'  Tendenz: EHER BULLISH ({len(bull_signals)} vs {len(bear_signals)} Signale)')
    elif len(bear_signals) > len(bull_signals):
        lines.append(f'  Tendenz: EHER BEARISH ({len(bear_signals)} vs {len(bull_signals)} Signale)')
    else:
        lines.append(f'  Tendenz: NEUTRAL ({len(bull_signals)} vs {len(bear_signals)} Signale)')

    return '\n'.join(lines)


# ============================================================================
# HTTP HANDLER
# ============================================================================

class EdgerunnerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        origin = self.headers.get('Origin', '')
        self.send_header('Access-Control-Allow-Origin', origin or '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Credentials', 'true')

    def _get_session_token(self):
        """Extract session token from Cookie header."""
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            part = part.strip()
            if part.startswith('edgerunner_session='):
                return part.split('=', 1)[1]
        return None

    def _get_user(self):
        """Get current authenticated user from session cookie."""
        token = self._get_session_token()
        if not token:
            return None
        return validate_session(token, _db_path)

    def _set_session_cookie(self, token, max_age=604800):
        """Set session cookie (7 days)."""
        self.send_header('Set-Cookie',
            f'edgerunner_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}')

    def _clear_session_cookie(self):
        """Clear session cookie."""
        self.send_header('Set-Cookie',
            'edgerunner_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _get_tf_param(self):
        """Extract ?tf= query parameter, default to active_tf."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        tf = params.get('tf', [None])[0]
        if tf and tf in TIMEFRAMES:
            return tf
        with _state_lock:
            return state['active_tf']

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        path = self.path.split('?')[0]
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'

        # --- Auth endpoints (public) ---
        if path == '/api/auth/register':
            try:
                params = json.loads(body.decode())
                username = params.get('username', '').strip()
                password = params.get('password', '')
                if not username or not password:
                    self._json({'error': 'Username and password required'}, 400)
                    return
                if len(password) < 4:
                    self._json({'error': 'Password must be at least 4 characters'}, 400)
                    return
                user = create_user(username, password, _db_path)
                if not user:
                    self._json({'error': 'Username already taken'}, 409)
                    return
                token = create_session(user['id'], path=_db_path)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self._cors()
                self._set_session_cookie(token)
                data = json.dumps({'username': user['username'], 'id': user['id']}).encode()
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self._json({'error': str(e)}, 400)

        elif path == '/api/auth/login':
            try:
                params = json.loads(body.decode())
                username = params.get('username', '').strip()
                password = params.get('password', '')
                user = authenticate_user(username, password, _db_path)
                if not user:
                    self._json({'error': 'Invalid credentials'}, 401)
                    return
                token = create_session(user['id'], path=_db_path)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self._cors()
                self._set_session_cookie(token)
                data = json.dumps({'username': user['username'], 'id': user['id']}).encode()
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self._json({'error': str(e)}, 400)

        elif path == '/api/auth/logout':
            token = self._get_session_token()
            if token:
                delete_session(token, _db_path)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self._clear_session_cookie()
            data = b'{"ok":true}'
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # --- Protected endpoints ---
        elif path == '/api/scenarios/filter':
            user = self._get_user()
            if not user:
                self._json({'error': 'Unauthorized'}, 401)
                return
            try:
                params = json.loads(body.decode())
                from scenarios import ScenarioManager
                sm = ScenarioManager(_db_path)
                tf = params.pop('timeframe', '1m')
                results = sm.filter(tf=tf, **params)
                self._json({'count': len(results), 'candles': results[:100]})
            except Exception as e:
                self._json({'error': str(e)}, 400)

        elif path == '/api/set_tf':
            user = self._get_user()
            if not user:
                self._json({'error': 'Unauthorized'}, 401)
                return
            try:
                params = json.loads(body.decode())
                tf = params.get('tf', '1m')
                if tf in TIMEFRAMES:
                    with _state_lock:
                        state['active_tf'] = tf
                        state['candles'] = state['candles_by_tf'].get(tf, [])
                        state['features']['values'] = state['features_by_tf'].get(tf, {})
                        state['structure'] = state['structure_by_tf'].get(tf, {
                            'swing_highs': [], 'swing_lows': [], 'bos_events': []
                        })
                    self._json({'ok': True, 'active_tf': tf})
                else:
                    self._json({'error': f'Unknown TF: {tf}'}, 400)
            except Exception as e:
                self._json({'error': str(e)}, 400)

        # --- Settings ---
        elif path == '/api/settings':
            user = self._get_user()
            if not user:
                self._json({'error': 'Unauthorized'}, 401)
                return
            try:
                params = json.loads(body.decode())
                # Validate
                allowed_keys = {'swing_lookback', 'macd_fast', 'macd_slow', 'macd_signal',
                                'rsi_period', 'atr_period', 'ema_periods', 'seeker_min_wick'}
                filtered = {k: v for k, v in params.items() if k in allowed_keys}
                if not filtered:
                    self._json({'error': 'No valid settings provided'}, 400)
                    return
                # Merge into current
                _current_settings.update(filtered)
                save_settings(_current_settings, _db_path)
                _rebuild_analyzers()
                self._json({'ok': True, 'settings': _current_settings})
            except Exception as e:
                self._json({'error': str(e)}, 400)

        # --- Scenario CRUD ---
        elif path == '/api/scenarios/create':
            user = self._get_user()
            if not user:
                self._json({'error': 'Unauthorized'}, 401)
                return
            try:
                params = json.loads(body.decode())
                from scenarios import ScenarioManager
                sm = ScenarioManager(_db_path)
                sid = sm.create(params['name'], params.get('description', ''))
                self._json({'ok': True, 'id': sid, 'name': params['name']})
            except Exception as e:
                self._json({'error': str(e)}, 400)

        elif path == '/api/scenarios/delete':
            user = self._get_user()
            if not user:
                self._json({'error': 'Unauthorized'}, 401)
                return
            try:
                params = json.loads(body.decode())
                from scenarios import ScenarioManager
                sm = ScenarioManager(_db_path)
                sm.delete(params['name'])
                self._json({'ok': True})
            except Exception as e:
                self._json({'error': str(e)}, 400)

        elif path == '/api/scenarios/add_candle':
            user = self._get_user()
            if not user:
                self._json({'error': 'Unauthorized'}, 401)
                return
            try:
                params = json.loads(body.decode())
                from scenarios import ScenarioManager
                sm = ScenarioManager(_db_path)
                sm.add_candle(
                    params['scenario'], params['timestamp'],
                    params.get('label', 'neutral'),
                    tf=params.get('timeframe', '1m'),
                    notes=params.get('notes')
                )
                self._json({'ok': True})
            except Exception as e:
                self._json({'error': str(e)}, 400)

        elif path == '/api/scenarios/add_filtered':
            user = self._get_user()
            if not user:
                self._json({'error': 'Unauthorized'}, 401)
                return
            try:
                params = json.loads(body.decode())
                from scenarios import ScenarioManager
                sm = ScenarioManager(_db_path)
                scenario = params.pop('scenario')
                label = params.pop('label', 'neutral')
                tf = params.pop('timeframe', '1m')
                count = sm.add_filtered(scenario, label, tf=tf, **params)
                self._json({'ok': True, 'added': count})
            except Exception as e:
                self._json({'error': str(e)}, 400)

        elif path == '/api/scenarios/compute_outcomes':
            user = self._get_user()
            if not user:
                self._json({'error': 'Unauthorized'}, 401)
                return
            try:
                params = json.loads(body.decode())
                from scenarios import ScenarioManager
                sm = ScenarioManager(_db_path)
                count = sm.compute_outcomes_bulk(
                    params['scenario'],
                    tf=params.get('timeframe', '1m')
                )
                self._json({'ok': True, 'computed': count})
            except Exception as e:
                self._json({'error': str(e)}, 400)

        # --- Pattern Matcher (no auth - read-only) ---
        elif path == '/api/pattern/match':
            try:
                params = json.loads(body.decode())
                from pattern_matcher import (find_matches, compute_match_stats,
                                             enrich_matches_with_whales,
                                             get_pattern_candles)
                criteria = params.get('criteria', [])
                tf = params.get('tf', '1m')
                limit = min(int(params.get('limit', 100)), 500)
                forward = min(int(params.get('forward_candles', 20)), 60)
                exclude_ts = params.get('exclude_ts')
                include_whales = params.get('include_whales', False)
                include_forward = params.get('include_forward', False)

                # Load reference candles for scoring
                reference_candles = None
                if exclude_ts:
                    max_lb = 0
                    for c in criteria:
                        if c['offset'] < 0:
                            max_lb = max(max_lb, abs(c['offset']))
                    ref_data = get_pattern_candles(
                        exclude_ts, max_lb, tf=tf, forward=0, path=_db_path
                    )
                    if ref_data:
                        reference_candles = ref_data['before'] + [ref_data['meta']]

                matches = find_matches(
                    criteria=criteria, tf=tf, limit=limit,
                    forward_candles=forward, exclude_ts=exclude_ts,
                    reference_candles=reference_candles,
                    path=_db_path
                )

                if include_whales and matches:
                    enrich_matches_with_whales(matches, tf=tf)

                stats = compute_match_stats(matches)

                slim_matches = []
                for m in matches:
                    sm = {
                        'meta': m['meta'],
                        'before': m['before'],
                        'outcome': m['outcome'],
                    }
                    if m.get('score') is not None:
                        sm['score'] = m['score']
                    if m.get('whale_context'):
                        sm['whale_context'] = m['whale_context']
                    if include_forward and m.get('after'):
                        sm['after'] = m['after']
                    slim_matches.append(sm)

                self._json({
                    'matches': slim_matches,
                    'stats': stats,
                    'criteria_used': criteria,
                })
            except Exception as e:
                traceback.print_exc()
                self._json({'error': str(e)}, 400)

        # --- Pattern Save/Delete ---
        elif path == '/api/pattern/save':
            try:
                params = json.loads(body.decode())
                name = params.get('name', '').strip()
                if not name:
                    self._json({'error': 'Pattern name required'}, 400)
                    return
                pid = save_pattern(
                    name=name,
                    description=params.get('description', ''),
                    tf=params.get('tf', '1m'),
                    lookback=int(params.get('lookback', 3)),
                    meta_ts=int(params.get('meta_ts', 0)),
                    criteria=params.get('criteria', []),
                    tags=params.get('tags', ''),
                    is_active=int(params.get('is_active', 1)),
                    path=_db_path,
                )
                self._json({'ok': True, 'id': pid, 'name': name})
            except Exception as e:
                traceback.print_exc()
                self._json({'error': str(e)}, 400)

        elif path == '/api/pattern/delete':
            try:
                params = json.loads(body.decode())
                name_or_id = params.get('name') or params.get('id')
                if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.isdigit()):
                    name_or_id = int(name_or_id)
                ok = delete_pattern(name_or_id, path=_db_path)
                self._json({'ok': ok})
            except Exception as e:
                self._json({'error': str(e)}, 400)

        # --- Signal Dismiss ---
        elif path == '/api/signals/dismiss':
            try:
                params = json.loads(body.decode())
                sid = int(params.get('id', 0))
                dismiss_signal(sid, path=_db_path)
                self._json({'ok': True})
            except Exception as e:
                self._json({'error': str(e)}, 400)

        # --- Auto-Discovery ---
        elif path == '/api/pattern/discover':
            try:
                params = json.loads(body.decode())
                from auto_discovery import discover_patterns
                disc_tf = params.get('tf', '1m')
                disc_lookback = int(params.get('lookback', 3))
                disc_forward = int(params.get('forward_candles', 20))
                disc_min_cluster = int(params.get('min_cluster_size', 5))
                disc_min_wr = float(params.get('min_win_rate', 55))
                disc_scan_limit = int(params.get('scan_limit', 10000))
                results = discover_patterns(
                    tf=disc_tf, lookback=disc_lookback,
                    forward_candles=disc_forward,
                    min_cluster_size=disc_min_cluster,
                    min_win_rate=disc_min_wr,
                    scan_limit=disc_scan_limit,
                    path=_db_path,
                )
                self._json({'discoveries': results})
            except Exception as e:
                traceback.print_exc()
                self._json({'error': str(e)}, 400)

        # --- AI Chat Analyze ---
        elif path == '/api/chat/analyze':
            try:
                params = json.loads(body.decode())
                message = params.get('message', '').strip()
                if not message:
                    self._json({'error': 'Message required'}, 400)
                    return

                meta_ts = int(params.get('meta_ts', 0))
                chat_tf = params.get('tf', '1m')
                chat_lookback = int(params.get('lookback', 3))
                chat_criteria = params.get('criteria', {})
                history = params.get('history', [])

                # Load candle data
                from pattern_matcher import get_pattern_candles
                candle_data = get_pattern_candles(
                    meta_ts, chat_lookback, tf=chat_tf,
                    forward=0, path=_db_path
                ) if meta_ts else None

                # === Pre-Analyse: Strukturierte Zusammenfassung bauen ===
                analysis = _build_candle_analysis(candle_data, chat_lookback)

                # Build criteria summary
                criteria_lines = []
                for pos_key, feats in chat_criteria.items():
                    if not isinstance(feats, dict):
                        continue
                    for feat, spec in feats.items():
                        if not isinstance(spec, dict) or not spec.get('enabled'):
                            continue
                        op = spec.get('op', '=')
                        val = spec.get('value', 0)
                        tol = spec.get('tolerance', 0)
                        if op == 'bool':
                            criteria_lines.append(f'  [{pos_key}] {feat} = bool({val})')
                        elif op == 'range':
                            criteria_lines.append(f'  [{pos_key}] {feat} = range({spec.get("min", 0)}-{spec.get("max", 0)})')
                        elif op == '=':
                            criteria_lines.append(f'  [{pos_key}] {feat} = {val} ±{tol}')
                        else:
                            criteria_lines.append(f'  [{pos_key}] {feat} {op} {val}')

                # Compose system prompt
                system_parts = [
                    'Du bist Edge, ein BTC Pattern Analyzer fuer Edgerunner.',
                    '',
                    'WICHTIGE REGELN:',
                    '1. Die Pre-Analyse wird dem User SEPARAT angezeigt. Er sieht die Zahlen bereits.',
                    '2. Wiederhole KEINE Zahlen, Preise oder Feature-Werte. Der User hat sie schon.',
                    '3. Gib nur: Einschaetzung, Interpretation, Zusammenhaenge, Criteria-Vorschlaege.',
                    '4. Widersprich der Pre-Analyse NIEMALS. Wenn dort BULLISH steht, ist es BULLISH.',
                    '5. Antworte auf Deutsch, knapp (3-6 Saetze max).',
                    '',
                ]

                if analysis:
                    system_parts.append('=== PRE-ANALYSE (FAKTEN — nicht widersprechen) ===')
                    system_parts.append(analysis)
                    system_parts.append('=== ENDE PRE-ANALYSE ===')
                    system_parts.append('')

                if criteria_lines:
                    system_parts.append('AKTIVE CRITERIA:')
                    system_parts.extend(criteria_lines)
                    system_parts.append('')

                system_parts.extend([
                    'Um Features als Criteria vorzuschlagen, nutze Tags:',
                    '  [TOGGLE:feature_name:operator:value:tolerance]',
                    'Beispiele:',
                    '  [TOGGLE:rsi14:range:0:30]     — RSI Range 0-30',
                    '  [TOGGLE:bos_bull:bool:1:0]    — BOS Bull = true',
                    '  [TOGGLE:vol_vs_ma:>:1.5:0]    — Volume > 1.5',
                    '  [TOGGLE:ema21_dist:=:0.5:0.2] — EMA21 Dist = 0.5 +-0.2',
                    '  [REMOVE:rsi14]                 — Feature entfernen',
                    '',
                    'Erklaere dem User was die Pre-Analyse zeigt und schlage passende Criteria vor.',
                ])

                system_prompt = '\n'.join(system_parts)

                # Build messages for Claude
                messages = [{'role': 'system', 'content': system_prompt}]
                for h in history[-10:]:  # Last 10 messages max
                    if h.get('role') in ('user', 'assistant'):
                        messages.append({'role': h['role'], 'content': h['content']})
                messages.append({'role': 'user', 'content': message})

                # Call Claude Proxy
                import re as _re
                CLAUDE_PROXY_URL = 'http://localhost:3456/v1/chat/completions'
                payload = json.dumps({
                    'model': 'claude-opus-4-6',
                    'messages': messages,
                    'temperature': 0.3,
                }).encode()
                req = urllib.request.Request(
                    CLAUDE_PROXY_URL,
                    data=payload,
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    claude_data = json.loads(resp.read().decode())

                reply_text = claude_data['choices'][0]['message']['content']
                model_used = claude_data.get('model', 'unknown')

                # Parse action tags (robust — skip malformed tags)
                actions = []
                TOGGLE_RE = _re.compile(r'\[TOGGLE:(\w+):([^:]+):([^:]+):([^\]]+)\]')
                REMOVE_RE = _re.compile(r'\[REMOVE:(\w+)\]')

                def _safe_num(s):
                    """Parse string to number, return None on failure."""
                    try:
                        return float(s) if '.' in s else int(s)
                    except (ValueError, TypeError):
                        return None

                for m in TOGGLE_RE.finditer(reply_text):
                    feat, op, val_str, tol_str = m.groups()
                    if op == 'range':
                        v_min = _safe_num(val_str)
                        v_max = _safe_num(tol_str)
                        if v_min is None or v_max is None:
                            continue
                        actions.append({
                            'action': 'toggle', 'feature': feat, 'op': op,
                            'min': v_min, 'max': v_max, 'value': 0, 'tolerance': 0,
                        })
                    elif op == 'bool':
                        # Accept 1/0, true/false, yes/no
                        val = 1 if val_str.lower() in ('1', 'true', 'yes') else 0
                        actions.append({
                            'action': 'toggle', 'feature': feat, 'op': 'bool',
                            'value': val, 'tolerance': 0,
                        })
                    else:
                        val = _safe_num(val_str)
                        tol = _safe_num(tol_str)
                        if val is None:
                            continue
                        actions.append({
                            'action': 'toggle', 'feature': feat, 'op': op,
                            'value': val, 'tolerance': tol or 0,
                        })

                for m in REMOVE_RE.finditer(reply_text):
                    actions.append({'action': 'remove', 'feature': m.group(1)})

                # Strip action tags from reply
                clean_reply = TOGGLE_RE.sub('', reply_text)
                clean_reply = REMOVE_RE.sub('', clean_reply).strip()

                self._json({
                    'reply': clean_reply,
                    'actions': actions,
                    'model': model_used,
                    'analysis': analysis or '',
                })
            except urllib.error.URLError as e:
                self._json({'error': f'Claude Proxy nicht erreichbar: {e}'}, 502)
            except Exception as e:
                traceback.print_exc()
                self._json({'error': str(e)}, 500)

        # --- Thread Control ---
        elif path == '/api/threads/control':
            user = self._get_user()
            if not user:
                self._json({'error': 'Unauthorized'}, 401)
                return
            try:
                params = json.loads(body.decode())
                action = params.get('action')
                thread_name = params.get('thread')
                if thread_name and thread_name in _pause_events:
                    if action == 'pause':
                        _pause_events[thread_name].clear()
                    elif action == 'resume':
                        _pause_events[thread_name].set()
                    elif action == 'set_interval':
                        interval = params.get('interval')
                        if interval and isinstance(interval, (int, float)) and interval > 0:
                            _poll_intervals[thread_name] = interval
                    self._json({'ok': True})
                elif action == 'enable_tf':
                    tf = params.get('tf')
                    if tf and tf in TIMEFRAMES:
                        _enabled_tfs.add(tf)
                        self._json({'ok': True})
                    else:
                        self._json({'error': f'Unknown TF: {tf}'}, 400)
                elif action == 'disable_tf':
                    tf = params.get('tf')
                    if tf and tf in TIMEFRAMES:
                        _enabled_tfs.discard(tf)
                        self._json({'ok': True})
                    else:
                        self._json({'error': f'Unknown TF: {tf}'}, 400)
                else:
                    self._json({'error': 'Invalid action or thread'}, 400)
            except Exception as e:
                self._json({'error': str(e)}, 400)

        else:
            self._json({'error': 'Not found'}, 404)

    def _serve_static(self, path):
        """Serve static files: dist/ first, then DASHBOARD_DIR, then SPA fallback."""
        if path == '/':
            path = '/index.html'
        elif path == '/legacy':
            path = '/edgerunner_dashboard.html'

        clean = os.path.normpath(path.lstrip('/'))
        if '..' in clean.split(os.sep):
            self._json({'error': 'Forbidden'}, 403)
            return

        ctypes = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
            '.woff2': 'font/woff2',
            '.woff': 'font/woff',
            '.ttf': 'font/ttf',
            '.map': 'application/json',
        }

        # Try dist/ first (Vite production build)
        filepath = os.path.join(DIST_DIR, clean)
        if os.path.isfile(filepath):
            ext = os.path.splitext(filepath)[1].lower()
            ctype = ctypes.get(ext, 'application/octet-stream')
            with open(filepath, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return

        # Try DASHBOARD_DIR (legacy files)
        filepath = os.path.join(DASHBOARD_DIR, clean)
        if os.path.isfile(filepath):
            ext = os.path.splitext(filepath)[1].lower()
            ctype = ctypes.get(ext, 'application/octet-stream')
            with open(filepath, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return

        # SPA fallback: serve dist/index.html for non-API, non-file paths
        spa_index = os.path.join(DIST_DIR, 'index.html')
        if os.path.isfile(spa_index):
            with open(spa_index, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return

        self._json({'error': 'Not found'}, 404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Auth me endpoint
        if path == '/api/auth/me':
            user = self._get_user()
            if user:
                self._json({'username': user['username'], 'user_id': user['user_id']})
            else:
                self._json({'error': 'Not authenticated'}, 401)
            return

        # --- Lock-free endpoints (no state needed) ---
        if path == '/api/settings':
            self._json({
                'settings': _current_settings,
                'defaults': DEFAULT_SETTINGS,
            })
            return

        if path == '/api/threads':
            thread_status = []
            for name in THREAD_NAMES:
                thread_status.append({
                    'name': name,
                    'running': _pause_events[name].is_set(),
                    'interval': _poll_intervals.get(name, 0),
                })
            self._json({
                'threads': thread_status,
                'enabled_tfs': sorted(_enabled_tfs),
                'all_tfs': TIMEFRAMES,
            })
            return

        if path == '/api/patterns':
            try:
                params = urllib.parse.parse_qs(parsed.query)
                active_only = params.get('active_only', ['false'])[0] == 'true'
                patterns = list_patterns(active_only=active_only, path=_db_path)
                self._json({'patterns': patterns})
            except Exception as e:
                self._json({'error': str(e)}, 500)
            return

        if path.startswith('/api/pattern/load/'):
            try:
                name = urllib.parse.unquote(path.split('/api/pattern/load/', 1)[1])
                if name.isdigit():
                    name = int(name)
                pat = load_pattern(name, path=_db_path)
                if pat:
                    self._json(pat)
                else:
                    self._json({'error': 'Pattern not found'}, 404)
            except Exception as e:
                self._json({'error': str(e)}, 400)
            return

        if path == '/api/signals':
            try:
                params = urllib.parse.parse_qs(parsed.query)
                hours = int(params.get('hours', [24])[0])
                signals = get_active_signals(hours=hours, path=_db_path)
                self._json({'signals': signals})
            except Exception as e:
                self._json({'error': str(e)}, 500)
            return

        if path == '/api/whales/live':
            try:
                import sqlite3 as _sq3
                tokyo_path = os.path.expanduser('~/shadow-tracker/shadow_tracker_tokyo.db')
                if not os.path.exists(tokyo_path):
                    self._json({'error': 'Tokyo DB not found'}, 404)
                    return
                params = urllib.parse.parse_qs(parsed.query)
                limit = min(int(params.get('limit', [50])[0]), 200)
                coin = params.get('coin', ['BTC'])[0]

                conn = _sq3.connect(tokyo_path)
                conn.row_factory = _sq3.Row

                # Latest whale activity
                rows = conn.execute('''
                    SELECT timestamp, coin, side, signal_type, notional_usd,
                           tier, win_rate, price_at_event, leverage,
                           pnl_1m, pnl_5m, pnl_15m, pnl_1h, pnl_4h
                    FROM whale_activity
                    WHERE coin = ?
                    ORDER BY timestamp DESC LIMIT ?
                ''', (coin, limit)).fetchall()
                activity = [dict(r) for r in rows]

                # Aggregate pressure (last 100 entries)
                recent = conn.execute('''
                    SELECT side, SUM(notional_usd) as vol, COUNT(*) as cnt,
                           AVG(pnl_1h) as avg_pnl_1h
                    FROM whale_activity
                    WHERE coin = ?
                    ORDER BY timestamp DESC LIMIT 100
                ''', (coin,)).fetchall()

                buys = conn.execute('''
                    SELECT SUM(notional_usd) as vol, COUNT(*) as cnt, AVG(pnl_1h) as avg_pnl
                    FROM (SELECT * FROM whale_activity WHERE coin = ? AND side = 'BUY'
                          ORDER BY timestamp DESC LIMIT 100)
                ''', (coin,)).fetchone()
                sells = conn.execute('''
                    SELECT SUM(notional_usd) as vol, COUNT(*) as cnt, AVG(pnl_1h) as avg_pnl
                    FROM (SELECT * FROM whale_activity WHERE coin = ? AND side = 'SELL'
                          ORDER BY timestamp DESC LIMIT 100)
                ''', (coin,)).fetchone()

                # Recent liquidations
                liqs = conn.execute('''
                    SELECT event_time, direction, liq_price, position_size_usd,
                           price_at_event, is_cascade_part
                    FROM liquidation_events
                    WHERE coin = ?
                    ORDER BY event_time DESC LIMIT 20
                ''', (coin,)).fetchall()

                conn.close()

                buy_vol = buys['vol'] or 0
                sell_vol = sells['vol'] or 0
                total = buy_vol + sell_vol

                pressure = {
                    'buy_count': buys['cnt'] or 0,
                    'sell_count': sells['cnt'] or 0,
                    'buy_volume': round(buy_vol, 2),
                    'sell_volume': round(sell_vol, 2),
                    'buy_pct': round(buy_vol / total * 100, 1) if total else 50,
                    'sell_pct': round(sell_vol / total * 100, 1) if total else 50,
                    'buy_avg_pnl_1h': round(buys['avg_pnl'] or 0, 4),
                    'sell_avg_pnl_1h': round(sells['avg_pnl'] or 0, 4),
                    'net': 'BULLISH' if buy_vol > sell_vol * 1.2 else
                           'BEARISH' if sell_vol > buy_vol * 1.2 else 'NEUTRAL',
                }

                self._json({
                    'activity': activity,
                    'pressure': pressure,
                    'liquidations': [dict(l) for l in liqs],
                })
            except Exception as e:
                traceback.print_exc()
                self._json({'error': str(e)}, 500)
            return

        if path == '/api/db/candles':
            params = urllib.parse.parse_qs(parsed.query)
            tf = params.get('tf', ['1m'])[0]
            if tf not in TIMEFRAMES:
                tf = '1m'
            page = int(params.get('page', [1])[0])
            limit = min(int(params.get('limit', [50])[0]), 200)
            order = params.get('order', ['desc'])[0]
            result = get_candles_paginated(tf=tf, page=page, limit=limit,
                                           order=order, path=_db_path)
            result['timeframe'] = tf
            self._json(result)
            return

        if path.startswith('/api/db/candle/') and path.endswith('/neighbors'):
            parts = path.split('/')
            try:
                ts = int(parts[4])
            except (ValueError, IndexError):
                self._json({'error': 'Invalid timestamp'}, 400)
                return
            params = urllib.parse.parse_qs(parsed.query)
            tf = params.get('tf', ['1m'])[0]
            count = int(params.get('range', [5])[0])
            result = get_candle_neighbors(ts, tf=tf, count=count, path=_db_path)
            result['timeframe'] = tf
            self._json(result)
            return

        if path.startswith('/api/scenarios/') and path.endswith('/stats'):
            parts = path.split('/')
            scenario_name = urllib.parse.unquote(parts[3])
            params = urllib.parse.parse_qs(parsed.query)
            tf = params.get('tf', ['1m'])[0]
            from scenarios import ScenarioManager
            sm = ScenarioManager(_db_path)
            stats_data = sm.compare_scenario(scenario_name, tf=tf)
            self._json(stats_data)
            return

        if path == '/api/pattern/candles':
            from pattern_matcher import get_pattern_candles
            params = urllib.parse.parse_qs(parsed.query)
            try:
                ts = int(params.get('ts', [0])[0])
                tf = params.get('tf', ['1m'])[0]
                lookback = int(params.get('lookback', [5])[0])
                forward = int(params.get('forward', [5])[0])
                result = get_pattern_candles(ts, lookback, tf=tf,
                                             forward=forward, path=_db_path)
                if result:
                    self._json(result)
                else:
                    self._json({'error': 'Candle not found'}, 404)
            except Exception as e:
                self._json({'error': str(e)}, 400)
            return

        with _state_lock:
            s = state

            if path == '/api/ticker':
                self._json(s['ticker'])

            elif path == '/api/candles':
                tf = self._get_tf_param()
                candles = s['candles_by_tf'].get(tf, [])
                slim = []
                for c in candles:
                    slim.append({
                        'time': c.get('time', c.get('timestamp', 0)),
                        'open': c['open'], 'high': c['high'],
                        'low': c['low'], 'close': c['close'],
                        'volume': c['volume'],
                        'ema21': c.get('ema21', 0), 'ema50': c.get('ema50', 0),
                    })
                self._json(slim)

            elif path == '/api/candles/latest':
                tf = self._get_tf_param()
                candles = s['candles_by_tf'].get(tf, [])
                latest = candles[-10:] if candles else []
                self._json(latest)

            elif path == '/api/features/status':
                tf = self._get_tf_param()
                features_val = s['features_by_tf'].get(tf, {})
                self._json({
                    'values': features_val,
                    'computed': 89,
                    'processing_ms': s['features']['processing_ms'],
                    'counter': s['features']['counter'],
                    'timeframe': tf,
                })

            elif path == '/api/features/stream':
                tf = self._get_tf_param()
                vals = s['features_by_tf'].get(tf, {})
                stream = []
                for name in FEATURE_NAMES:
                    v = vals.get(name, 0)
                    stream.append({'name': name, 'value': v})
                self._json(stream)

            elif path == '/api/neural/status':
                self._json(s['neural'])

            elif path == '/api/structure':
                tf = self._get_tf_param()
                struct = dict(s['structure_by_tf'].get(tf, {
                    'swing_highs': [], 'swing_lows': [], 'bos_events': []
                }))
                candles = s['candles_by_tf'].get(tf, [])
                if candles:
                    c = candles[-1]
                    struct['current_candle'] = {
                        'open': c['open'], 'high': c['high'],
                        'low': c['low'], 'close': c['close'],
                        'volume': round(c.get('volume', 0), 2),
                        'delta': round(c.get('delta', 0), 2),
                        'is_bullish': c['close'] >= c['open'],
                        'ema21': round(c.get('ema21', 0), 2),
                        'ema50': round(c.get('ema50', 0), 2),
                    }
                struct['timeframe'] = tf
                self._json(struct)

            elif path == '/api/liquidations':
                # Proxy to Shadow Tracker liq_api on port 5558
                try:
                    req = urllib.request.Request('http://127.0.0.1:5558/predict?coin=BTC')
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode())
                    self._json(data)
                except Exception:
                    self._json({'stop_predictions': [], 'current_price': 0, 'error': 'shadow-tracker offline'})

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

            elif path == '/api/timeframes':
                # List all TFs with counts and active status
                tf_stats = get_all_tf_stats(_db_path)
                for ts in tf_stats:
                    ts['active'] = ts['tf'] == s['active_tf']
                    ts['live_candles'] = len(s['candles_by_tf'].get(ts['tf'], []))
                self._json(tf_stats)

            elif path == '/api/db/stats':
                tf = self._get_tf_param()
                ts_range = get_ts_range(tf=tf, path=_db_path)
                self._json({
                    'candles': count_candles(tf=tf, path=_db_path),
                    'db_path': _db_path,
                    'min_ts': ts_range[0] if ts_range else None,
                    'max_ts': ts_range[1] if ts_range else None,
                    'timeframe': tf,
                    'engine': 'CandleAnalyzer (real)',
                    'source': 'Hyperliquid (live)',
                })

            elif path.startswith('/api/db/candle/'):
                tf = self._get_tf_param()
                try:
                    ts = int(path.split('/')[-1])
                    candle = get_candle_by_ts(ts, tf=tf, path=_db_path)
                    if candle:
                        self._json(candle)
                    else:
                        self._json({'error': 'Not found'}, 404)
                except (ValueError, IndexError):
                    self._json({'error': 'Invalid timestamp'}, 400)

            elif path == '/api/scenarios':
                from scenarios import ScenarioManager
                sm = ScenarioManager(_db_path)
                self._json(sm.list_all())

            else:
                # Static file serving — prefer dist/ (Vite build), fallback to legacy
                self._serve_static(path)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print()
    print('  Edgerunner Dashboard Server (Multi-TF)')
    print('  ' + '─' * 42)
    print(f'  URL:        http://0.0.0.0:{PORT}')
    print(f'  Whales:     {WHALE_DATA_PATH}')
    print(f'  Features:   {len(FEATURE_NAMES)}')
    print(f'  DB:         {_db_path}')
    print(f'  Timeframes: {", ".join(TIMEFRAMES)}')
    print(f'  Live:       Hyperliquid API')
    print(f'  History:    Binance API')
    print()

    # Print TF stats
    for s in get_all_tf_stats(_db_path):
        if s['count'] > 0:
            print(f"    {s['tf']:>3s}: {s['count']:>10,} candles in DB")
    print()

    # Start daemon threads
    threads = [
        ('HL Multi-TF Poller', _hyperliquid_poller),
        ('Ticker Poller', _ticker_poller),
        ('Whale Loader', _whale_data_loader),
        ('System Health', _system_health_monitor),
        ('Signal Updater', _signal_updater),
        ('Pattern Detector', _pattern_detector),
    ]

    for name, target in threads:
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        print(f'  > {name} started')

    print()
    print(f'  Dashboard: http://192.168.0.20:{PORT}')
    print()

    server = ThreadingHTTPServer(('0.0.0.0', PORT), EdgerunnerHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Edgerunner stopped')
        server.server_close()
