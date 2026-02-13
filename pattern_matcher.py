#!/usr/bin/env python3
"""
Edgerunner Pattern Matcher — Sequence-based backtester with whale integration.

Workflow:
1. User selects a meta candle (timestamp + tf)
2. User defines criteria for N lookback positions (feature ± tolerance)
3. System scans history for matching candle sequences
4. For each match: compute outcomes + whale context
"""
import os
import sqlite3
import statistics
from typing import Optional

from db import (DEFAULT_DB_PATH, FEATURE_COLUMNS, NUMERIC_FEATURES,
                _connect, _table_name)

TOKYO_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', 'shadow-tracker', 'shadow_tracker_tokyo.db')

# Timeframe → milliseconds per candle
TF_MS = {
    '1m': 60_000, '5m': 300_000, '15m': 900_000, '30m': 1_800_000,
    '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000,
    '1w': 604_800_000, '1M': 2_592_000_000,
}


def get_pattern_candles(meta_ts: int, lookback: int, tf: str = '1m',
                        forward: int = 5, path: str = DEFAULT_DB_PATH) -> dict:
    """Load meta candle + lookback + forward candles with all 89 features.

    Returns {meta: dict, before: [dict], after: [dict]} or None.
    """
    table = _table_name(tf)
    conn = _connect(path)

    meta = conn.execute(
        f'SELECT * FROM {table} WHERE timestamp = ?', (meta_ts,)
    ).fetchone()

    if not meta:
        conn.close()
        return None

    before = conn.execute(
        f'SELECT * FROM {table} WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?',
        (meta_ts, lookback)
    ).fetchall()

    after = conn.execute(
        f'SELECT * FROM {table} WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?',
        (meta_ts, forward)
    ).fetchall()

    conn.close()
    return {
        'meta': dict(meta),
        'before': [dict(r) for r in reversed(before)],
        'after': [dict(r) for r in after],
    }


def _candle_matches_criteria(candle: dict, features: dict) -> bool:
    """Check if a single candle matches feature criteria with tolerance."""
    for feat, spec in features.items():
        val = candle.get(feat)
        if val is None:
            return False

        target = spec['value']
        tol = spec.get('tolerance', 0)

        if isinstance(target, (int, float)) and isinstance(val, (int, float)):
            if abs(val - target) > tol:
                return False
        else:
            if val != target:
                return False
    return True


def _build_where_clause(features: dict):
    """Build SQL WHERE conditions from feature criteria with tolerance.

    Returns (conditions_str, params_list).
    """
    conditions = []
    params = []

    for feat, spec in features.items():
        if feat not in FEATURE_COLUMNS:
            continue
        target = spec['value']
        tol = spec.get('tolerance', 0)

        if isinstance(target, (int, float)):
            if tol == 0:
                conditions.append(f'{feat} = ?')
                params.append(target)
            else:
                conditions.append(f'{feat} BETWEEN ? AND ?')
                params.append(target - tol)
                params.append(target + tol)
        else:
            conditions.append(f'{feat} = ?')
            params.append(target)

    return ' AND '.join(conditions) if conditions else '1=1', params


def find_matches(criteria: list, tf: str = '1m', limit: int = 200,
                 forward_candles: int = 20, exclude_ts: int = None,
                 path: str = DEFAULT_DB_PATH) -> list:
    """Find all candle sequences matching the criteria pattern.

    Args:
        criteria: list of {offset: int, features: {name: {value, tolerance}}}
                  offset 0 = meta candle, -1 = one before, etc.
        tf: timeframe
        limit: max matches to return
        forward_candles: how many candles after meta to include for outcomes
        exclude_ts: timestamp to exclude (the original meta candle)
        path: DB path

    Returns: list of match dicts with meta, lookback, forward candles.
    """
    table = _table_name(tf)

    # Separate position 0 criteria from lookback
    pos0_criteria = None
    lookback_criteria = []
    for c in criteria:
        if c['offset'] == 0:
            pos0_criteria = c['features']
        else:
            lookback_criteria.append(c)

    # Sort lookback by offset (most negative first)
    lookback_criteria.sort(key=lambda x: x['offset'])

    # Find max lookback depth needed
    max_lookback = 0
    if lookback_criteria:
        max_lookback = abs(min(c['offset'] for c in lookback_criteria))

    conn = _connect(path)

    # Step 1: Find all candidates matching position 0
    if pos0_criteria:
        where, params = _build_where_clause(pos0_criteria)
    else:
        where, params = '1=1', []

    if exclude_ts:
        where += ' AND timestamp != ?'
        params.append(exclude_ts)

    candidates = conn.execute(
        f'SELECT * FROM {table} WHERE {where} ORDER BY timestamp DESC LIMIT ?',
        params + [limit * 3]  # over-fetch since some won't match lookback
    ).fetchall()

    matches = []

    for cand in candidates:
        if len(matches) >= limit:
            break

        cand_dict = dict(cand)
        cand_ts = cand_dict['timestamp']

        # Step 2: Get lookback candles for this candidate
        if max_lookback > 0:
            before_rows = conn.execute(
                f'SELECT * FROM {table} WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?',
                (cand_ts, max_lookback)
            ).fetchall()
            before = [dict(r) for r in reversed(before_rows)]
        else:
            before = []

        # Verify we have enough lookback candles
        if len(before) < max_lookback:
            continue

        # Step 3: Check each lookback position
        all_match = True
        for lc in lookback_criteria:
            idx = len(before) + lc['offset']  # e.g. offset -1 → last element
            if idx < 0 or idx >= len(before):
                all_match = False
                break
            if not _candle_matches_criteria(before[idx], lc['features']):
                all_match = False
                break

        if not all_match:
            continue

        # Step 4: Get forward candles for outcomes
        after_rows = conn.execute(
            f'SELECT * FROM {table} WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?',
            (cand_ts, forward_candles)
        ).fetchall()

        # Compute outcomes
        price = cand_dict['close']
        after = [dict(r) for r in after_rows]
        outcome = _compute_outcome(price, after, tf)

        matches.append({
            'meta': cand_dict,
            'before': before[-max_lookback:] if max_lookback > 0 else [],
            'after': after,
            'outcome': outcome,
        })

    conn.close()
    return matches


def _compute_outcome(entry_price: float, forward: list, tf: str) -> dict:
    """Compute price outcome statistics from forward candles."""
    if not forward or not entry_price:
        return {}

    outcome = {}
    tf_ms = TF_MS.get(tf, 60_000)

    # P&L at specific offsets
    offsets = {'5c': 5, '10c': 10, '20c': 20}
    for label, count in offsets.items():
        if len(forward) >= count:
            fp = forward[count - 1]['close']
            outcome[f'pnl_{label}'] = (fp - entry_price) / entry_price * 100
            outcome[f'price_{label}'] = fp

    # Max favorable / adverse excursion
    if forward:
        highs = [c['high'] for c in forward if c.get('high')]
        lows = [c['low'] for c in forward if c.get('low')]
        if highs:
            outcome['max_favorable'] = (max(highs) - entry_price) / entry_price * 100
        if lows:
            outcome['max_adverse'] = (entry_price - min(lows)) / entry_price * 100

    # Direction: was the move up or down after?
    if forward:
        last = forward[-1]['close']
        outcome['direction'] = 'LONG' if last > entry_price else 'SHORT'
        outcome['total_pnl'] = (last - entry_price) / entry_price * 100

    return outcome


def compute_match_stats(matches: list) -> dict:
    """Aggregate statistics across all matches."""
    if not matches:
        return {'total': 0}

    pnl_keys = ['pnl_5c', 'pnl_10c', 'pnl_20c', 'total_pnl']
    stats = {'total': len(matches)}

    for key in pnl_keys:
        vals = [m['outcome'].get(key) for m in matches if m['outcome'].get(key) is not None]
        if vals:
            wins = [v for v in vals if v > 0]
            stats[key] = {
                'avg': round(statistics.mean(vals), 4),
                'median': round(statistics.median(vals), 4),
                'std': round(statistics.stdev(vals), 4) if len(vals) > 1 else 0,
                'min': round(min(vals), 4),
                'max': round(max(vals), 4),
                'win_rate': round(len(wins) / len(vals) * 100, 1),
                'count': len(vals),
            }

    fav = [m['outcome'].get('max_favorable') for m in matches
           if m['outcome'].get('max_favorable') is not None]
    adv = [m['outcome'].get('max_adverse') for m in matches
           if m['outcome'].get('max_adverse') is not None]

    if fav:
        stats['avg_max_favorable'] = round(statistics.mean(fav), 4)
    if adv:
        stats['avg_max_adverse'] = round(statistics.mean(adv), 4)

    # Direction distribution
    dirs = [m['outcome'].get('direction') for m in matches if m['outcome'].get('direction')]
    if dirs:
        stats['long_pct'] = round(dirs.count('LONG') / len(dirs) * 100, 1)
        stats['short_pct'] = round(dirs.count('SHORT') / len(dirs) * 100, 1)

    return stats


# =============================================================================
# WHALE INTEGRATION — Tokyo Shadow Tracker DB
# =============================================================================

def get_whale_context(candle_ts_ms: int, tf: str = '1m',
                      tokyo_path: str = TOKYO_DB_PATH) -> dict:
    """Get whale activity and liquidations near a candle timestamp.

    Args:
        candle_ts_ms: candle timestamp in milliseconds
        tf: timeframe (determines search window)
        tokyo_path: path to Tokyo shadow tracker DB

    Returns: {whale_activity: [...], liquidations: [...], summary: {...}}
    """
    if not os.path.exists(tokyo_path):
        return {'whale_activity': [], 'liquidations': [], 'summary': None,
                'error': 'Tokyo DB not found'}

    # Search window = 2x the timeframe duration
    window_ms = TF_MS.get(tf, 60_000) * 2
    ts_sec = candle_ts_ms / 1000.0
    window_sec = window_ms / 1000.0

    conn = sqlite3.connect(tokyo_path)
    conn.row_factory = sqlite3.Row

    # Whale activity (timestamp is float unix seconds)
    whales = conn.execute('''
        SELECT timestamp, coin, side, signal_type, notional_usd,
               tier, win_rate, price_at_event, leverage, entry_price, liq_price,
               pnl_1m, pnl_5m, pnl_15m, pnl_1h, pnl_4h
        FROM whale_activity
        WHERE coin = 'BTC' AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp
    ''', (ts_sec - window_sec, ts_sec + window_sec)).fetchall()

    # Liquidation events (event_time is text datetime)
    from datetime import datetime, timezone
    dt_start = datetime.fromtimestamp((ts_sec - window_sec), tz=timezone.utc)
    dt_end = datetime.fromtimestamp((ts_sec + window_sec), tz=timezone.utc)

    liqs = conn.execute('''
        SELECT event_time, coin, direction, liq_price, position_size_usd,
               price_at_event, is_cascade_part
        FROM liquidation_events
        WHERE coin = 'BTC' AND event_time BETWEEN ? AND ?
        ORDER BY event_time
    ''', (dt_start.strftime('%Y-%m-%d %H:%M:%S'),
          dt_end.strftime('%Y-%m-%d %H:%M:%S'))).fetchall()

    conn.close()

    whale_list = [dict(w) for w in whales]
    liq_list = [dict(l) for l in liqs]

    # Summary
    summary = None
    if whale_list or liq_list:
        buys = [w for w in whale_list if w['side'] == 'BUY']
        sells = [w for w in whale_list if w['side'] == 'SELL']
        buy_vol = sum(w['notional_usd'] or 0 for w in buys)
        sell_vol = sum(w['notional_usd'] or 0 for w in sells)
        liq_long = [l for l in liq_list if l['direction'] == 'LONG']
        liq_short = [l for l in liq_list if l['direction'] == 'SHORT']

        summary = {
            'whale_buys': len(buys),
            'whale_sells': len(sells),
            'buy_volume_usd': round(buy_vol, 2),
            'sell_volume_usd': round(sell_vol, 2),
            'net_sentiment': 'BULLISH' if buy_vol > sell_vol * 1.2 else
                             'BEARISH' if sell_vol > buy_vol * 1.2 else 'NEUTRAL',
            'liquidations_long': len(liq_long),
            'liquidations_short': len(liq_short),
            'total_liq_usd': round(sum(l['position_size_usd'] or 0 for l in liq_list), 2),
        }

    return {
        'whale_activity': whale_list,
        'liquidations': liq_list,
        'summary': summary,
    }


def enrich_matches_with_whales(matches: list, tf: str = '1m',
                                tokyo_path: str = TOKYO_DB_PATH) -> list:
    """Add whale context to each match."""
    for m in matches:
        ts = m['meta']['timestamp']
        m['whale_context'] = get_whale_context(ts, tf, tokyo_path)
    return matches


if __name__ == '__main__':
    # Quick test
    from db import get_candle_neighbors
    n = get_candle_neighbors(0, '5m')
    print(f'DB has data: {n}')
