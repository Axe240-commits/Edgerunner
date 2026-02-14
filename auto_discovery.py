#!/usr/bin/env python3
"""
Edgerunner Auto-Discovery — Finds recurring candle patterns via quantized clustering.

Algorithm:
1. Load last N candles
2. Normalize 18 key features
3. Quantize into bins → build cluster keys
4. For clusters >= min_size: compute forward outcomes
5. Filter by win rate, sort by edge quality
6. Return top discoveries with suggested criteria
"""
import statistics
from collections import defaultdict

from db import DEFAULT_DB_PATH, _connect, _table_name

# Key features to cluster on (18 features)
DISCOVERY_FEATURES = [
    'body_ratio', 'wick_ratio', 'body_position', 'is_bullish',
    'delta_pct', 'vol_vs_ma',
    'bos_bull', 'bos_bear', 'choch',
    'break_depth', 'swing_age_norm',
    'bull_div', 'bear_div',
    'is_seeker_div', 'is_seeker_kill',
    'rsi14', 'ema21_dist', 'atr14',
]

# Number of quantization bins per feature
NUM_BINS = 4


def _quantize(value, min_val, max_val, bins=NUM_BINS):
    """Quantize a value into a bin index."""
    if max_val == min_val:
        return 0
    normalized = (value - min_val) / (max_val - min_val)
    normalized = max(0.0, min(1.0, normalized))
    b = int(normalized * bins)
    return min(b, bins - 1)


def discover_patterns(tf: str = '1m', lookback: int = 3,
                      forward_candles: int = 20,
                      min_cluster_size: int = 5,
                      min_win_rate: float = 55.0,
                      scan_limit: int = 10000,
                      path: str = DEFAULT_DB_PATH) -> list:
    """Discover recurring patterns via quantized feature clustering.

    Returns list of discovered pattern dicts, sorted by edge quality.
    """
    table = _table_name(tf)
    conn = _connect(path)

    # Load last N candles
    cols_needed = ['timestamp', 'close', 'high', 'low'] + DISCOVERY_FEATURES
    cols_str = ','.join(cols_needed)

    rows = conn.execute(
        f'SELECT {cols_str} FROM {table} ORDER BY timestamp DESC LIMIT ?',
        (scan_limit,)
    ).fetchall()
    conn.close()

    if len(rows) < forward_candles + lookback + 10:
        return []

    # Reverse to chronological order
    candles = [dict(r) for r in reversed(rows)]

    # Compute feature stats for normalization
    feat_stats = {}
    for feat in DISCOVERY_FEATURES:
        vals = [c.get(feat) for c in candles if c.get(feat) is not None]
        if not vals:
            feat_stats[feat] = (0, 1)
        else:
            feat_stats[feat] = (min(vals), max(vals))

    # Build cluster keys for each candle (excluding last forward_candles)
    clusters = defaultdict(list)
    scan_end = len(candles) - forward_candles

    for i in range(lookback, scan_end):
        # Build quantized key from the candle at position i
        c = candles[i]
        key_parts = []
        for feat in DISCOVERY_FEATURES:
            val = c.get(feat)
            if val is None:
                val = 0
            mn, mx = feat_stats[feat]
            key_parts.append(_quantize(val, mn, mx))

        cluster_key = tuple(key_parts)
        clusters[cluster_key].append(i)

    # Evaluate clusters
    discoveries = []

    for key, indices in clusters.items():
        if len(indices) < min_cluster_size:
            continue

        # Compute forward outcomes for each member
        pnl_values = []
        directions = []
        sample_timestamps = []

        for idx in indices:
            entry_price = candles[idx]['close']
            if not entry_price:
                continue

            # Forward candles
            fwd = candles[idx + 1: idx + 1 + forward_candles]
            if len(fwd) < 5:
                continue

            last_close = fwd[-1]['close']
            pnl = (last_close - entry_price) / entry_price * 100
            pnl_values.append(pnl)
            directions.append('LONG' if pnl > 0 else 'SHORT')
            sample_timestamps.append(candles[idx]['timestamp'])

        if len(pnl_values) < min_cluster_size:
            continue

        wins = [p for p in pnl_values if p > 0]
        win_rate = len(wins) / len(pnl_values) * 100

        if win_rate < min_win_rate:
            continue

        avg_pnl = statistics.mean(pnl_values)
        long_pct = directions.count('LONG') / len(directions) * 100

        # Compute average feature values for this cluster
        avg_features = {}
        for feat in DISCOVERY_FEATURES:
            vals = [candles[idx].get(feat, 0) for idx in indices
                    if candles[idx].get(feat) is not None]
            if vals:
                avg_features[feat] = round(statistics.mean(vals), 4)

        # Build suggested criteria
        suggested_criteria = []
        for feat in DISCOVERY_FEATURES:
            vals = [candles[idx].get(feat, 0) for idx in indices
                    if candles[idx].get(feat) is not None]
            if not vals:
                continue
            mean = statistics.mean(vals)
            if len(vals) > 1:
                std = statistics.stdev(vals)
            else:
                std = 0

            # Binary features
            if feat in ('is_bullish', 'bos_bull', 'bos_bear', 'choch',
                        'bull_div', 'bear_div', 'is_seeker_div', 'is_seeker_kill'):
                majority = round(mean)
                if abs(mean - majority) < 0.3:  # strong consensus
                    suggested_criteria.append({
                        'feature': feat,
                        'op': 'bool',
                        'value': majority,
                    })
            else:
                # Continuous: use range around mean ± 1 std
                if std > 0:
                    suggested_criteria.append({
                        'feature': feat,
                        'op': 'range',
                        'min': round(mean - std, 4),
                        'max': round(mean + std, 4),
                    })
                else:
                    suggested_criteria.append({
                        'feature': feat,
                        'op': '=',
                        'value': round(mean, 4),
                        'tolerance': abs(mean * 0.1) if mean != 0 else 0.1,
                    })

        # Edge quality score
        size_capped = min(len(pnl_values), 50)
        edge_quality = win_rate * size_capped

        direction = 'LONG' if long_pct > 60 else 'SHORT' if long_pct < 40 else 'NEUTRAL'

        # Top discriminative features (highest variance reduction)
        top_features = sorted(
            [(feat, avg_features.get(feat, 0)) for feat in DISCOVERY_FEATURES
             if feat in avg_features],
            key=lambda x: abs(x[1]),
            reverse=True,
        )[:6]

        discoveries.append({
            'cluster_key': [int(k) for k in key],
            'size': len(pnl_values),
            'win_rate': round(win_rate, 1),
            'avg_pnl': round(avg_pnl, 4),
            'direction': direction,
            'long_pct': round(long_pct, 1),
            'edge_quality': round(edge_quality, 1),
            'avg_features': avg_features,
            'suggested_criteria': suggested_criteria,
            'top_features': [{'name': f, 'value': v} for f, v in top_features],
            'sample_timestamps': sample_timestamps[:5],
        })

    # Sort by edge quality (descending)
    discoveries.sort(key=lambda d: d['edge_quality'], reverse=True)

    return discoveries[:20]
