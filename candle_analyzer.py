#!/usr/bin/env python3
"""
Edgerunner Candle Analyzer — Feature Engine.

Computes ALL 89 features for every candle from raw OHLCV+Delta data.
Deterministic: same input = same output.

Feature groups:
  1-7   Rohdaten          — direct from input
  8-15  Anatomie          — body, wicks, range
  16-18 Volume/Delta      — relative to SMA-20
  19-25 Swing Structure   — swing H/L, BOS, CHoCH
  26-34 Break Quality     — body/wick break, depth, age
  35-47 Paarung           — breaker vs swing candle comparison
  48-50 Kette             — recursive break chains
  51-53 Cluster           — simultaneous breaks
  54-61 MACD + Div        — Eddiecator formula
  62-71 Seeker            — ported from seeker_theory.py
  72-77 Kontext/Trend     — EMAs, ATR, RSI, VWAP
  78-81 Multi-TF          — 15m/1h aggregation
  82-89 Whale             — NULL for history, live from Shadow Tracker
"""
import json
import math


# =============================================================================
# TECHNICAL INDICATORS
# =============================================================================

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


def compute_vwap(candles):
    """Cumulative VWAP reset per day (UTC). Returns list of VWAP values."""
    result = []
    cum_vol = 0.0
    cum_tp_vol = 0.0
    current_day = None
    for c in candles:
        # Day boundary (timestamp is ms)
        day = c['timestamp'] // 86400000
        if day != current_day:
            cum_vol = 0.0
            cum_tp_vol = 0.0
            current_day = day
        tp = (c['high'] + c['low'] + c['close']) / 3.0
        cum_vol += c['volume']
        cum_tp_vol += tp * c['volume']
        result.append(cum_tp_vol / cum_vol if cum_vol > 0 else c['close'])
    return result


# =============================================================================
# SWING DETECTION
# =============================================================================

def detect_swings(candles, lookback=2):
    """Detect swing highs and lows. Returns (swing_highs, swing_lows).
    Each entry: {'index': i, 'price': p, 'time': ts, 'candle': candle_dict}
    Left neighbours use strict < / > so on ties the leftmost candle wins.
    Right neighbours use <= / >= so the level is still recognised.
    """
    swing_highs = []
    swing_lows = []
    for i in range(lookback, len(candles) - lookback):
        h = candles[i]['high']
        l = candles[i]['low']
        # Left strict (<) → earlier candle with same high blocks this one
        # Right loose (<=) → later candle with same high doesn't block
        is_sh = all(candles[i - j]['high'] < h for j in range(1, lookback + 1)) and \
                all(candles[i + j]['high'] <= h for j in range(1, lookback + 1))
        is_sl = all(candles[i - j]['low'] > l for j in range(1, lookback + 1)) and \
                all(candles[i + j]['low'] >= l for j in range(1, lookback + 1))
        if is_sh:
            swing_highs.append({
                'index': i, 'price': h,
                'time': candles[i].get('timestamp', 0),
                'candle': candles[i],
            })
        if is_sl:
            swing_lows.append({
                'index': i, 'price': l,
                'time': candles[i].get('timestamp', 0),
                'candle': candles[i],
            })
    return swing_highs, swing_lows


# =============================================================================
# SEEKER DETECTION (ported core logic from seeker_theory.py)
# =============================================================================

MIN_WICK_PERCENT = 0.20  # 20% of range (default, overridable via CandleAnalyzer)


def _is_seeker_hs(candle, min_wick=None):
    """Check if candle qualifies as High Seeker (significant upper wick)."""
    threshold = min_wick if min_wick is not None else MIN_WICK_PERCENT
    rng = candle['high'] - candle['low']
    if rng < 1e-8:
        return False
    body_high = max(candle['open'], candle['close'])
    upper_wick = candle['high'] - body_high
    return (upper_wick / rng) >= threshold


def _is_seeker_ls(candle, min_wick=None):
    """Check if candle qualifies as Low Seeker (significant lower wick)."""
    threshold = min_wick if min_wick is not None else MIN_WICK_PERCENT
    rng = candle['high'] - candle['low']
    if rng < 1e-8:
        return False
    body_low = min(candle['open'], candle['close'])
    lower_wick = body_low - candle['low']
    return (lower_wick / rng) >= threshold


def _seeker_wick_zone(candle, seeker_type):
    """Get wick zone (top, bottom) for a seeker candle.
    HS: body_high to high
    LS: low to body_low
    """
    body_high = max(candle['open'], candle['close'])
    body_low = min(candle['open'], candle['close'])
    if seeker_type == 'HS':
        return (candle['high'], body_high)  # (top, bottom)
    else:
        return (body_low, candle['low'])  # (top, bottom)


def _check_seeker_div(seeker_candle, seeker_type, test_candle):
    """Check if test_candle creates a divergence in the seeker's wick zone.
    HS div: candle touches zone + body_high > seeker.body_high
    LS div: candle touches zone + body_low < seeker.body_low
    """
    zone_top, zone_bottom = _seeker_wick_zone(seeker_candle, seeker_type)
    # Touch check: candle range overlaps zone
    if test_candle['high'] < zone_bottom or test_candle['low'] > zone_top:
        return False
    test_body_high = max(test_candle['open'], test_candle['close'])
    test_body_low = min(test_candle['open'], test_candle['close'])
    if seeker_type == 'HS':
        seeker_body_high = max(seeker_candle['open'], seeker_candle['close'])
        return test_body_high > seeker_body_high and test_candle['close'] >= zone_bottom
    else:
        seeker_body_low = min(seeker_candle['open'], seeker_candle['close'])
        return test_body_low < seeker_body_low and test_candle['close'] <= zone_top


def _check_seeker_kill(seeker_candle, seeker_type, test_candle):
    """Check if test_candle kills the seeker.
    HS kill: close > seeker.high
    LS kill: close < seeker.low
    """
    if seeker_type == 'HS':
        return test_candle['close'] > seeker_candle['high']
    else:
        return test_candle['close'] < seeker_candle['low']


# =============================================================================
# MACD DIVERGENCE DETECTION (Eddiecator formula)
# =============================================================================

def _find_macd_peaks_troughs(macd_vals):
    """Find MACD peaks (local max > 0) and troughs (local min < 0).
    Peak: 2+ bars falling in each direction, all above 0.
    Trough: 2+ bars rising in each direction, all below 0.
    Returns (peaks, troughs) — lists of {'index': i, 'value': v}
    """
    peaks = []
    troughs = []
    for i in range(2, len(macd_vals) - 2):
        v = macd_vals[i]
        # Peak: above 0, higher than neighbors
        if v > 0 and macd_vals[i-1] < v and macd_vals[i-2] < macd_vals[i-1] and \
           macd_vals[i+1] < v and macd_vals[i+2] < macd_vals[i+1]:
            peaks.append({'index': i, 'value': v})
        # Trough: below 0, lower than neighbors
        if v < 0 and macd_vals[i-1] > v and macd_vals[i-2] > macd_vals[i-1] and \
           macd_vals[i+1] > v and macd_vals[i+2] > macd_vals[i+1]:
            troughs.append({'index': i, 'value': v})
    return peaks, troughs


def _detect_macd_divergences(candles, macd_vals):
    """Detect bull/bear MACD divergences using Eddiecator formula.
    Bull div: Price Lower Low + MACD Higher Low (troughs)
    Bear div: Price Higher High + MACD Lower High (peaks)
    Returns per-candle: list of {'bull_div': 0/1, 'bear_div': 0/1, 'div_strength': f, 'div_width': n}
    """
    n = len(candles)
    divs = [{'bull_div': 0, 'bear_div': 0, 'div_strength': 0.0, 'div_width': 0} for _ in range(n)]
    peaks, troughs = _find_macd_peaks_troughs(macd_vals)

    # Bear divergences: compare consecutive peaks
    for j in range(1, len(peaks)):
        p1 = peaks[j - 1]
        p2 = peaks[j]
        i1, i2 = p1['index'], p2['index']
        if i2 >= n or i1 >= n:
            continue
        # Price HH + MACD LH
        price_hh = candles[i2]['high'] > candles[i1]['high']
        macd_lh = p2['value'] < p1['value']
        if price_hh and macd_lh:
            width = i2 - i1
            strength = abs(p1['value'] - p2['value']) / max(abs(p1['value']), 1e-8)
            divs[i2]['bear_div'] = 1
            divs[i2]['div_strength'] = min(1.0, strength)
            divs[i2]['div_width'] = width

    # Bull divergences: compare consecutive troughs
    for j in range(1, len(troughs)):
        t1 = troughs[j - 1]
        t2 = troughs[j]
        i1, i2 = t1['index'], t2['index']
        if i2 >= n or i1 >= n:
            continue
        # Price LL + MACD HL
        price_ll = candles[i2]['low'] < candles[i1]['low']
        macd_hl = t2['value'] > t1['value']  # HL = less negative
        if price_ll and macd_hl:
            width = i2 - i1
            strength = abs(t1['value'] - t2['value']) / max(abs(t1['value']), 1e-8)
            divs[i2]['bull_div'] = 1
            divs[i2]['div_strength'] = max(divs[i2]['div_strength'], min(1.0, strength))
            divs[i2]['div_width'] = max(divs[i2]['div_width'], width)

    return divs


# =============================================================================
# CANDLE ANALYZER
# =============================================================================

class CandleAnalyzer:
    """Computes all 89 features for candles.

    Args:
        swing_lookback: Bars left/right for swing detection (default 2)
        macd_fast: MACD fast EMA period (default 5)
        macd_slow: MACD slow EMA period (default 13)
        macd_signal: MACD signal period (default 1)
        rsi_period: RSI period (default 14)
        atr_period: ATR period (default 14)
        ema_periods: Tuple of EMA periods (default (21, 50, 200))
        seeker_min_wick: Min wick % of range for seeker detection (default 0.20)
    """

    def __init__(self, swing_lookback=2, seeker_swing_lookback=3,
                 macd_fast=5, macd_slow=13, macd_signal=1,
                 rsi_period=14, atr_period=14, ema_periods=(21, 50, 200),
                 seeker_min_wick=0.20, vol_sma_period=10):
        self.swing_lookback = swing_lookback
        self.seeker_swing_lookback = seeker_swing_lookback
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.ema_periods = ema_periods
        self.seeker_min_wick = seeker_min_wick
        self.vol_sma_period = vol_sma_period

        # State for incremental mode
        self._history = []  # All candles seen so far
        self._swings = {'highs': [], 'lows': []}
        self._trend_direction = 0  # 1=bull, -1=bear
        self._active_seekers = []  # Active seeker cycles
        self._last_bos_type = None

    def analyze_batch(self, raw_candles):
        """Compute all 89 features for a batch of candles.

        Input: list of {'timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta'}
        Output: list of dicts with all 89 feature keys
        """
        n = len(raw_candles)
        if n == 0:
            return []

        # Pre-compute series indicators
        closes = [c['close'] for c in raw_candles]
        volumes = [c['volume'] for c in raw_candles]
        deltas = [c.get('delta', 0) for c in raw_candles]

        ema21 = ema(closes, self.ema_periods[0])
        ema50 = ema(closes, self.ema_periods[1])
        ema200 = ema(closes, self.ema_periods[2])
        rsi_vals = compute_rsi(closes, self.rsi_period)
        atr_vals = compute_atr(raw_candles, self.atr_period)
        macd_vals, macd_sig = compute_macd(closes, self.macd_fast, self.macd_slow, self.macd_signal)
        vol_sma = sma(volumes, self.vol_sma_period)
        delta_sma = sma([abs(d) for d in deltas], self.vol_sma_period)
        vwap_vals = compute_vwap(raw_candles)

        # Swing detection
        swing_highs, swing_lows = detect_swings(raw_candles, self.swing_lookback)

        # Build swing index lookup
        sh_set = {sh['index'] for sh in swing_highs}
        sl_set = {sl['index'] for sl in swing_lows}

        # BOS detection with enhanced data
        bos_data = self._compute_bos_full(raw_candles, swing_highs, swing_lows)

        # MACD peak/trough detection
        macd_peaks_set = set()
        macd_troughs_set = set()
        for i in range(3, n):
            # Peak: 3 falling bars, all > 0 (from PLAN.md Eddiecator formula)
            if (macd_vals[i] > 0 and macd_vals[i] < macd_vals[i-1] < macd_vals[i-2] < macd_vals[i-3]
                    and macd_vals[i-1] > 0 and macd_vals[i-2] > 0 and macd_vals[i-3] > 0):
                macd_peaks_set.add(i - 1)  # Peak was at i-1 (before the 3 falling)
            # Trough: 3 rising bars, all < 0
            if (macd_vals[i] < 0 and macd_vals[i] > macd_vals[i-1] > macd_vals[i-2] > macd_vals[i-3]
                    and macd_vals[i-1] < 0 and macd_vals[i-2] < 0 and macd_vals[i-3] < 0):
                macd_troughs_set.add(i - 1)

        # MACD divergences
        macd_divs = _detect_macd_divergences(raw_candles, macd_vals)

        # Seeker tracking — uses stricter swing detection (lookback=3)
        if self.seeker_swing_lookback != self.swing_lookback:
            seeker_sh, seeker_sl = detect_swings(raw_candles, self.seeker_swing_lookback)
            seeker_sh_set = {s['index'] for s in seeker_sh}
            seeker_sl_set = {s['index'] for s in seeker_sl}
        else:
            seeker_sh, seeker_sl = swing_highs, swing_lows
            seeker_sh_set, seeker_sl_set = sh_set, sl_set
        seeker_features = self._compute_seekers_batch(raw_candles, seeker_sh, seeker_sl, seeker_sh_set, seeker_sl_set)

        # Multi-TF: HTF features default to 0 (each TF is now standalone)
        htf_default = {'htf_trend': 0, 'htf_swing_high': 0.0, 'htf_swing_low': 0.0, 'htf_bos': 0}

        # Now build feature dicts
        results = []
        for i in range(n):
            c = raw_candles[i]
            atr = atr_vals[i] if i < len(atr_vals) else 1.0
            if atr < 1e-8:
                atr = 1.0

            f = {}

            # --- Rohdaten (1-7) ---
            f['timestamp'] = c['timestamp']
            f['open'] = c['open']
            f['high'] = c['high']
            f['low'] = c['low']
            f['close'] = c['close']
            f['volume'] = c['volume']
            f['delta'] = c.get('delta', 0)

            # --- Anatomie (8-15) ---
            body = abs(c['close'] - c['open'])
            rng = c['high'] - c['low']
            body_high = max(c['open'], c['close'])
            body_low = min(c['open'], c['close'])
            upper_wick = c['high'] - body_high
            lower_wick = body_low - c['low']

            f['body_size'] = body
            f['upper_wick'] = upper_wick
            f['lower_wick'] = lower_wick
            f['total_range'] = rng
            f['body_ratio'] = body / rng if rng > 0 else 0.5
            f['wick_ratio'] = upper_wick / lower_wick if lower_wick > 0 else (1.0 if upper_wick == 0 else 10.0)
            f['body_position'] = (c['close'] - c['low']) / rng if rng > 0 else 0.5
            f['is_bullish'] = 1 if c['close'] >= c['open'] else 0

            # --- Volume/Delta (16-18) ---
            delta_val = c.get('delta', 0)
            vol = c['volume']
            f['delta_pct'] = delta_val / vol if vol > 0 else 0.0
            vs = vol_sma[i] if i < len(vol_sma) else vol
            f['vol_vs_ma'] = vol / vs if vs > 0 else 1.0
            ds = delta_sma[i] if i < len(delta_sma) else max(abs(delta_val), 1e-8)
            f['delta_vs_ma'] = abs(delta_val) / ds if ds > 0 else 1.0

            # --- Swing Structure (19-25) ---
            f['is_swing_high'] = 1 if i in sh_set else 0
            f['is_swing_low'] = 1 if i in sl_set else 0

            bos = bos_data[i]
            f['bos_bull'] = bos['bos_bull']
            f['bos_bear'] = bos['bos_bear']
            f['choch'] = bos['choch']

            # Distance to nearest swing high/low (ATR normalized)
            last_sh = self._find_last_swing(swing_highs, i)
            last_sl = self._find_last_swing(swing_lows, i)
            f['dist_swing_high'] = (last_sh['price'] - c['close']) / atr if last_sh else 0.0
            f['dist_swing_low'] = (c['close'] - last_sl['price']) / atr if last_sl else 0.0

            # --- Break Quality (26-34) ---
            f['bos_body'] = bos['bos_body']
            f['bos_wick'] = bos['bos_wick']
            f['break_depth'] = bos['break_depth'] / atr if atr > 0 else 0.0
            f['swing_age'] = bos['swing_age']
            f['broken_swing_ts'] = bos['broken_swing_ts']
            f['swing_age_norm'] = bos['swing_age'] / 200.0
            f['breaks_highs'] = bos['breaks_highs']
            f['breaks_lows'] = bos['breaks_lows']
            f['max_age_broken'] = bos['max_age_broken']
            f['min_age_broken'] = bos['min_age_broken']

            # --- Paarung (35-47) ---
            pair = bos.get('pair', {})
            f['sw_body_ratio'] = pair.get('sw_body_ratio', 0.0)
            f['sw_wick_ratio'] = pair.get('sw_wick_ratio', 0.0)
            f['sw_delta_pct'] = pair.get('sw_delta_pct', 0.0)
            f['sw_vol_rel'] = pair.get('sw_vol_rel', 0.0)
            f['sw_bullish'] = pair.get('sw_bullish', 0)
            f['sw_body_pos'] = pair.get('sw_body_pos', 0.0)
            f['sw_ohlc'] = pair.get('sw_ohlc')
            f['vol_ratio_bsw'] = pair.get('vol_ratio_bsw', 0.0)
            f['delta_ratio_bsw'] = pair.get('delta_ratio_bsw', 0.0)
            f['body_ratio_bsw'] = pair.get('body_ratio_bsw', 0.0)
            f['same_dir'] = pair.get('same_dir', 0)
            f['broken_was_seeker'] = pair.get('broken_was_seeker', 0)
            f['broken_was_seeker_div'] = pair.get('broken_was_seeker_div', 0)

            # --- Kette (48-50) ---
            chain = bos.get('chain', {})
            f['swing_had_break'] = chain.get('swing_had_break', 0)
            f['chain_depth'] = chain.get('chain_depth', 0)
            f['prev_swing_features'] = chain.get('prev_swing_features')

            # --- Cluster (51-53) ---
            cluster = bos.get('cluster', {})
            f['cluster_range'] = cluster.get('cluster_range', 0.0)
            f['cluster_range_atr'] = cluster.get('cluster_range', 0.0) / atr if atr > 0 else 0.0
            f['cluster_spread'] = cluster.get('cluster_spread', 0)

            # --- MACD + Div (54-61) ---
            f['macd_line'] = macd_vals[i] if i < len(macd_vals) else 0.0
            f['macd_peak'] = 1 if i in macd_peaks_set else 0
            f['macd_trough'] = 1 if i in macd_troughs_set else 0
            md = macd_divs[i]
            f['bull_div'] = md['bull_div']
            f['bear_div'] = md['bear_div']
            f['div_near_daily'] = 0  # TODO: needs daily H/L data
            f['div_strength'] = md['div_strength']
            f['div_width'] = md['div_width']

            # --- Seeker (62-71) ---
            sk = seeker_features[i]
            f['is_seeker_hs'] = sk['is_seeker_hs']
            f['is_seeker_ls'] = sk['is_seeker_ls']
            f['is_seeker_div'] = sk['is_seeker_div']
            f['seeker_div_nr'] = sk['seeker_div_nr']
            f['dist_prev_seeker_div'] = sk['dist_prev_seeker_div']
            f['dist_prev_seeker_div_norm'] = sk['dist_prev_seeker_div'] / 200.0 if sk['dist_prev_seeker_div'] > 0 else 0.0
            f['is_seeker_kill'] = sk['is_seeker_kill']
            f['killed_seeker_divs'] = sk['killed_seeker_divs']
            f['killed_seeker_ts'] = sk['killed_seeker_ts']
            f['killed_seekers_count'] = sk['killed_seekers_count']
            f['killed_seekers_ages'] = sk['killed_seekers_ages']
            f['killed_seekers_oldest_ts'] = sk['killed_seekers_oldest_ts']
            f['killed_seekers_newest_ts'] = sk['killed_seekers_newest_ts']
            f['killed_seekers_span_bars'] = sk['killed_seekers_span_bars']
            f['killed_seekers_age_min'] = sk['killed_seekers_age_min']
            f['killed_seekers_age_max'] = sk['killed_seekers_age_max']
            f['killed_seekers_age_avg'] = sk['killed_seekers_age_avg']
            f['candle_was_seeker'] = sk['candle_was_seeker']
            f['candle_was_seeker_div'] = sk['candle_was_seeker_div']

            # --- Kontext/Trend (72-77) ---
            f['ema21_dist'] = (c['close'] - ema21[i]) / atr if i < len(ema21) else 0.0
            f['ema50_dist'] = (c['close'] - ema50[i]) / atr if i < len(ema50) else 0.0
            f['ema200_dist'] = (c['close'] - ema200[i]) / atr if i < len(ema200) else 0.0
            f['atr14'] = atr
            f['rsi14'] = rsi_vals[i] if i < len(rsi_vals) else 50.0
            vwap_val = vwap_vals[i] if i < len(vwap_vals) else c['close']
            f['vwap_dist'] = (c['close'] - vwap_val) / atr if atr > 0 else 0.0

            # --- Multi-TF (78-81) — defaults, can be overridden by set_htf_context ---
            f['htf_trend'] = htf_default['htf_trend']
            f['htf_swing_high'] = htf_default['htf_swing_high']
            f['htf_swing_low'] = htf_default['htf_swing_low']
            f['htf_bos'] = htf_default['htf_bos']

            # --- Whale Features (82-89) — NULL for batch/history ---
            f['whale_sentiment'] = None
            f['whale_confidence'] = None
            f['bull_pressure'] = None
            f['bear_pressure'] = None
            f['whale_cluster'] = None
            f['whale_cluster_strength'] = None
            f['whale_cluster_dir'] = None
            f['elite_whale_active'] = None

            results.append(f)

        return results

    def analyze_incremental(self, new_candle):
        """For live operation: analyze a single new candle with maintained state.
        Appends to internal history and returns feature dict for latest candle.
        """
        self._history.append(new_candle)
        # Keep max 500 candles in history for memory efficiency
        if len(self._history) > 500:
            self._history = self._history[-500:]
        # Re-analyze the full history window
        features = self.analyze_batch(self._history)
        return features[-1] if features else {}

    # =========================================================================
    # BOS DETECTION (enhanced with break quality, pairing, chain, cluster)
    # =========================================================================

    def _compute_bos_full(self, candles, swing_highs, swing_lows):
        """Compute BOS with full quality metrics per candle."""
        n = len(candles)
        empty = {
            'bos_bull': 0, 'bos_bear': 0, 'choch': 0,
            'bos_body': 0, 'bos_wick': 0, 'break_depth': 0.0,
            'swing_age': 0, 'breaks_highs': 0, 'breaks_lows': 0,
            'max_age_broken': 0, 'min_age_broken': 0,
            'broken_swing_ts': 0,
            'pair': {}, 'chain': {}, 'cluster': {},
        }
        result = [dict(empty) for _ in range(n)]

        # Track which swing levels have been broken (to avoid re-triggering)
        broken_sh_levels = set()
        broken_sl_levels = set()
        trend_dir = 0  # 1 = bull, -1 = bear
        last_bos_type = None

        # Build index -> swing_candle mapping for chain/pairing
        sh_by_index = {sh['index']: sh for sh in swing_highs}
        sl_by_index = {sl['index']: sl for sl in swing_lows}

        # Track which swing candles were seekers (for broken_was_seeker)
        seeker_hs_indices = set()
        seeker_ls_indices = set()
        seeker_div_indices = set()  # candles that generated seeker divs
        for sh in swing_highs:
            if _is_seeker_hs(sh['candle'], self.seeker_min_wick):
                seeker_hs_indices.add(sh['index'])
        for sl in swing_lows:
            if _is_seeker_ls(sl['candle'], self.seeker_min_wick):
                seeker_ls_indices.add(sl['index'])

        for i, c in enumerate(candles):
            # Check all unbroken swing highs
            broken_highs_now = []
            for sh in swing_highs:
                if sh['index'] >= i:
                    continue
                level_key = (sh['index'], sh['price'])
                if level_key in broken_sh_levels:
                    continue
                if c['close'] > sh['price']:
                    broken_sh_levels.add(level_key)
                    broken_highs_now.append(sh)

            # Check all unbroken swing lows
            broken_lows_now = []
            for sl in swing_lows:
                if sl['index'] >= i:
                    continue
                level_key = (sl['index'], sl['price'])
                if level_key in broken_sl_levels:
                    continue
                if c['close'] < sl['price']:
                    broken_sl_levels.add(level_key)
                    broken_lows_now.append(sl)

            r = result[i]

            # BOS Bull
            if broken_highs_now:
                r['bos_bull'] = 1
                # Use the most recent (nearest) broken swing
                primary = max(broken_highs_now, key=lambda s: s['index'])
                r['swing_age'] = i - primary['index']
                r['broken_swing_ts'] = primary['time']
                r['break_depth'] = c['close'] - primary['price']
                r['bos_body'] = 1 if c['open'] < primary['price'] else 0
                r['bos_wick'] = 1 if r['bos_body'] == 0 else 0
                r['breaks_highs'] = len(broken_highs_now)
                ages = [i - s['index'] for s in broken_highs_now]
                r['max_age_broken'] = max(ages) if ages else 0
                r['min_age_broken'] = min(ages) if ages else 0

                # CHoCH: first BOS against prior trend
                if trend_dir == -1 and last_bos_type == 'bear':
                    r['choch'] = 1
                trend_dir = 1
                last_bos_type = 'bull'

                # Pairing: breaker candle vs swing candle
                sw_candle = primary['candle']
                r['pair'] = self._compute_pair(c, sw_candle, i, primary['index'],
                                               seeker_hs_indices, seeker_ls_indices,
                                               seeker_div_indices)

                # Chain: did the swing candle itself break something?
                r['chain'] = self._compute_chain(primary['index'], result)

                # Cluster
                if len(broken_highs_now) > 1:
                    prices = [s['price'] for s in broken_highs_now]
                    r['cluster'] = {
                        'cluster_range': max(prices) - min(prices),
                        'cluster_spread': max(ages) - min(ages) if len(ages) > 1 else 0,
                    }

            # BOS Bear
            if broken_lows_now:
                r['bos_bear'] = 1
                primary = max(broken_lows_now, key=lambda s: s['index'])
                if r['broken_swing_ts'] == 0:
                    r['broken_swing_ts'] = primary['time']
                if r['swing_age'] == 0:  # don't overwrite if both triggered
                    r['swing_age'] = i - primary['index']
                r['break_depth'] = max(r['break_depth'], primary['price'] - c['close'])
                if r['bos_body'] == 0:
                    r['bos_body'] = 1 if c['open'] > primary['price'] else 0
                    r['bos_wick'] = 1 if r['bos_body'] == 0 else 0
                r['breaks_lows'] = len(broken_lows_now)
                ages = [i - s['index'] for s in broken_lows_now]
                r['max_age_broken'] = max(r['max_age_broken'], max(ages) if ages else 0)
                r['min_age_broken'] = min(ages) if ages else r['min_age_broken']

                if trend_dir == 1 and last_bos_type == 'bull' and r['choch'] == 0:
                    r['choch'] = 1
                trend_dir = -1
                last_bos_type = 'bear'

                sw_candle = primary['candle']
                if not r.get('pair'):
                    r['pair'] = self._compute_pair(c, sw_candle, i, primary['index'],
                                                   seeker_hs_indices, seeker_ls_indices,
                                                   seeker_div_indices)

                if not r.get('chain', {}).get('swing_had_break'):
                    r['chain'] = self._compute_chain(primary['index'], result)

                if len(broken_lows_now) > 1:
                    prices = [s['price'] for s in broken_lows_now]
                    cl = {
                        'cluster_range': max(prices) - min(prices),
                        'cluster_spread': max(ages) - min(ages) if len(ages) > 1 else 0,
                    }
                    # Merge with existing cluster
                    if r['cluster'].get('cluster_range', 0) > 0:
                        r['cluster']['cluster_range'] = max(r['cluster']['cluster_range'], cl['cluster_range'])
                        r['cluster']['cluster_spread'] = max(r['cluster']['cluster_spread'], cl['cluster_spread'])
                    else:
                        r['cluster'] = cl

        return result

    def _compute_pair(self, breaker, sw_candle, breaker_idx, swing_idx,
                      seeker_hs_set, seeker_ls_set, seeker_div_set):
        """Compute Paarung features: breaker candle vs swing candle."""
        sw_rng = sw_candle['high'] - sw_candle['low']
        sw_body = abs(sw_candle['close'] - sw_candle['open'])
        sw_body_high = max(sw_candle['open'], sw_candle['close'])
        sw_body_low = min(sw_candle['open'], sw_candle['close'])
        sw_upper = sw_candle['high'] - sw_body_high
        sw_lower = sw_body_low - sw_candle['low']

        br_body = abs(breaker['close'] - breaker['open'])
        br_vol = breaker.get('volume', 0)
        sw_vol = sw_candle.get('volume', 1e-8)
        br_delta = breaker.get('delta', 0)
        sw_delta = sw_candle.get('delta', 0)

        return {
            'sw_body_ratio': sw_body / sw_rng if sw_rng > 0 else 0.5,
            'sw_wick_ratio': sw_upper / sw_lower if sw_lower > 0 else (1.0 if sw_upper == 0 else 10.0),
            'sw_delta_pct': sw_delta / sw_vol if sw_vol > 0 else 0.0,
            'sw_vol_rel': sw_vol / max(br_vol, 1e-8),
            'sw_bullish': 1 if sw_candle['close'] >= sw_candle['open'] else 0,
            'sw_body_pos': (sw_candle['close'] - sw_candle['low']) / sw_rng if sw_rng > 0 else 0.5,
            'sw_ohlc': json.dumps([sw_candle['open'], sw_candle['high'],
                                    sw_candle['low'], sw_candle['close']]),
            'vol_ratio_bsw': br_vol / sw_vol if sw_vol > 0 else 1.0,
            'delta_ratio_bsw': br_delta / sw_delta if abs(sw_delta) > 1e-8 else 0.0,
            'body_ratio_bsw': br_body / sw_body if sw_body > 0 else 1.0,
            'same_dir': 1 if (breaker['close'] >= breaker['open']) == (sw_candle['close'] >= sw_candle['open']) else 0,
            'broken_was_seeker': 1 if (swing_idx in seeker_hs_set or swing_idx in seeker_ls_set) else 0,
            'broken_was_seeker_div': 1 if swing_idx in seeker_div_set else 0,
        }

    def _compute_chain(self, swing_idx, bos_results):
        """Check if the swing candle at swing_idx itself had a BOS event."""
        if swing_idx < 0 or swing_idx >= len(bos_results):
            return {'swing_had_break': 0, 'chain_depth': 0, 'prev_swing_features': None}
        prev_bos = bos_results[swing_idx]
        had_break = 1 if (prev_bos['bos_bull'] or prev_bos['bos_bear']) else 0
        depth = 0
        if had_break:
            depth = 1 + prev_bos.get('chain', {}).get('chain_depth', 0)
        prev_feat = None
        if had_break and prev_bos.get('pair'):
            prev_feat = json.dumps({
                'sw_body_ratio': prev_bos['pair'].get('sw_body_ratio', 0),
                'vol_ratio_bsw': prev_bos['pair'].get('vol_ratio_bsw', 0),
                'break_depth': prev_bos.get('break_depth', 0),
            })
        return {
            'swing_had_break': had_break,
            'chain_depth': depth,
            'prev_swing_features': prev_feat,
        }

    # =========================================================================
    # SEEKER COMPUTATION (batch)
    # =========================================================================

    def _compute_seekers_batch(self, candles, swing_highs, swing_lows, sh_set, sl_set):
        """Compute seeker features for each candle in batch."""
        n = len(candles)
        empty = {
            'is_seeker_hs': 0, 'is_seeker_ls': 0,
            'is_seeker_div': 0, 'seeker_div_nr': 0,
            'dist_prev_seeker_div': 0,
            'is_seeker_kill': 0, 'killed_seeker_divs': 0, 'killed_seeker_ts': 0,
            'killed_seekers_count': 0, 'killed_seekers_ages': '[]',
            'killed_seekers_oldest_ts': 0, 'killed_seekers_newest_ts': 0,
            'killed_seekers_span_bars': 0,
            'killed_seekers_age_min': 0, 'killed_seekers_age_max': 0,
            'killed_seekers_age_avg': 0.0,
            'candle_was_seeker': 0, 'candle_was_seeker_div': 0,
        }
        result = [dict(empty) for _ in range(n)]

        # Track active seekers: list of {type, candle, index, divs: [], div_indices: []}
        active_seekers = []
        last_div_index = -1  # For dist_prev_seeker_div

        # Also track which candle indices are seeker origins / div candles
        seeker_origin_indices = set()
        seeker_div_candle_indices = set()

        for i in range(n):
            c = candles[i]

            # 1. Check kills first
            killed_seekers = []
            remaining = []
            for sk in active_seekers:
                if _check_seeker_kill(sk['candle'], sk['type'], c):
                    killed_seekers.append(sk)
                else:
                    remaining.append(sk)
            active_seekers = remaining

            if killed_seekers:
                result[i]['is_seeker_kill'] = 1
                result[i]['killed_seekers_count'] = len(killed_seekers)

                # Sum of divs across all killed seekers
                result[i]['killed_seeker_divs'] = sum(len(sk['divs']) for sk in killed_seekers)

                # Keep backward-compatible representative ts (most divs)
                best_killed = max(killed_seekers, key=lambda sk: len(sk['divs']))
                result[i]['killed_seeker_ts'] = best_killed['candle'].get('timestamp', 0)

                # Full kill age/time window stats for Battle Card + later analysis
                ages = [max(0, i - sk.get('index', i)) for sk in killed_seekers]
                origin_ts = [int(sk['candle'].get('timestamp', 0) or 0) for sk in killed_seekers]
                origin_ts = [t for t in origin_ts if t > 0]

                result[i]['killed_seekers_ages'] = json.dumps(ages)
                result[i]['killed_seekers_age_min'] = min(ages) if ages else 0
                result[i]['killed_seekers_age_max'] = max(ages) if ages else 0
                result[i]['killed_seekers_age_avg'] = (sum(ages) / len(ages)) if ages else 0.0

                if origin_ts:
                    result[i]['killed_seekers_oldest_ts'] = min(origin_ts)
                    result[i]['killed_seekers_newest_ts'] = max(origin_ts)

                if ages:
                    result[i]['killed_seekers_span_bars'] = max(ages) - min(ages)

            # 2. Check for divergences on active seekers
            for sk in active_seekers:
                if _check_seeker_div(sk['candle'], sk['type'], c):
                    sk['divs'].append(i)
                    sk['div_indices'].append(i)
                    result[i]['is_seeker_div'] = 1
                    result[i]['seeker_div_nr'] = len(sk['divs'])
                    if last_div_index >= 0:
                        result[i]['dist_prev_seeker_div'] = i - last_div_index
                    last_div_index = i
                    seeker_div_candle_indices.add(i)

            # 3. Detect new seekers at swing points
            if i in sh_set and _is_seeker_hs(c, self.seeker_min_wick):
                result[i]['is_seeker_hs'] = 1
                active_seekers.append({
                    'type': 'HS', 'candle': c, 'index': i,
                    'divs': [], 'div_indices': [],
                })
                seeker_origin_indices.add(i)
            if i in sl_set and _is_seeker_ls(c, self.seeker_min_wick):
                result[i]['is_seeker_ls'] = 1
                active_seekers.append({
                    'type': 'LS', 'candle': c, 'index': i,
                    'divs': [], 'div_indices': [],
                })
                seeker_origin_indices.add(i)

            # 4. Mark whether this candle was a seeker or seeker div
            result[i]['candle_was_seeker'] = 1 if i in seeker_origin_indices else 0
            result[i]['candle_was_seeker_div'] = 1 if i in seeker_div_candle_indices else 0

        return result

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _find_last_swing(swings, current_idx):
        """Find the most recent swing before current_idx."""
        last = None
        for s in swings:
            if s['index'] < current_idx:
                last = s
        return last


if __name__ == '__main__':
    import urllib.request
    import json as _json

    print('Fetching 200 candles from Binance...')
    url = 'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=200'
    req = urllib.request.Request(url, headers={'User-Agent': 'Edgerunner/1.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = _json.loads(resp.read().decode())

    candles = []
    for k in raw:
        candles.append({
            'timestamp': k[0],
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5]),
            'delta': float(k[9]) * 2 - float(k[5]),  # taker_buy_vol * 2 - volume
        })

    analyzer = CandleAnalyzer()
    features = analyzer.analyze_batch(candles)

    print(f'\nAnalyzed {len(features)} candles')
    if features:
        last = features[-1]
        print(f'\nLatest candle features ({len(last)} keys):')
        for k, v in last.items():
            if v is not None:
                print(f'  {k:30s} = {v}')
            else:
                print(f'  {k:30s} = NULL')

        # Count non-None features
        filled = sum(1 for v in last.values() if v is not None)
        print(f'\nFilled: {filled}/89, NULL (whale): {89 - filled}/89')

        # Check for any BOS/Seeker events in the batch
        bos_bulls = sum(1 for f in features if f['bos_bull'])
        bos_bears = sum(1 for f in features if f['bos_bear'])
        seekers_hs = sum(1 for f in features if f['is_seeker_hs'])
        seekers_ls = sum(1 for f in features if f['is_seeker_ls'])
        seeker_kills = sum(1 for f in features if f['is_seeker_kill'])
        bull_divs = sum(1 for f in features if f['bull_div'])
        bear_divs = sum(1 for f in features if f['bear_div'])
        print(f'\nEvents in 200 candles:')
        print(f'  BOS Bull: {bos_bulls}, BOS Bear: {bos_bears}')
        print(f'  Seeker HS: {seekers_hs}, Seeker LS: {seekers_ls}, Kills: {seeker_kills}')
        print(f'  Bull Div: {bull_divs}, Bear Div: {bear_divs}')
