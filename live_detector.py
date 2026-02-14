#!/usr/bin/env python3
"""
Edgerunner Live Pattern Detector — Checks saved patterns against new candles.

Used by the Pattern Detector thread in edgerunner_server.py.
"""
import json
import time

from db import (DEFAULT_DB_PATH, _connect, list_patterns, save_signal,
                get_active_signals, dismiss_signal)
from pattern_matcher import find_matches, compute_match_stats


def check_patterns_for_candle(candle_ts: int, tf: str,
                              path: str = DEFAULT_DB_PATH) -> list:
    """Check all active saved patterns against a specific candle timestamp.

    Returns list of signal dicts for matches found.
    """
    patterns = list_patterns(active_only=True, path=path)
    signals = []

    for pat in patterns:
        if pat['tf'] != tf:
            continue

        criteria = pat.get('criteria', [])
        if not criteria:
            continue

        # Run match against just this candle
        # We search with limit=1 and check if the specific ts matches
        matches = find_matches(
            criteria=criteria,
            tf=tf,
            limit=5,
            forward_candles=0,
            exclude_ts=pat.get('meta_ts'),
            path=path,
        )

        # Check if any match is at (or very near) the target candle_ts
        for m in matches:
            match_ts = m['meta']['timestamp']
            # Only consider recent matches (within last 2 candle periods)
            if match_ts != candle_ts:
                continue

            score = m.get('score', 100.0)
            direction = m.get('outcome', {}).get('direction', '')
            price = m['meta'].get('close', 0)

            signals.append({
                'pattern_id': pat['id'],
                'pattern_name': pat['name'],
                'match_ts': match_ts,
                'match_score': score,
                'direction_bias': direction or _infer_direction(pat),
                'tf': tf,
                'price_at_signal': price,
            })

    return signals


def _infer_direction(pattern: dict) -> str:
    """Infer direction bias from pattern stats."""
    wr = pattern.get('last_match_win_rate')
    if wr is not None:
        return 'LONG' if wr > 55 else 'SHORT' if wr < 45 else 'NEUTRAL'
    return 'NEUTRAL'


def save_detected_signals(signals: list, path: str = DEFAULT_DB_PATH) -> int:
    """Save detected signals to DB. Returns count saved."""
    count = 0
    for s in signals:
        try:
            save_signal(
                pattern_id=s['pattern_id'],
                pattern_name=s['pattern_name'],
                match_ts=s['match_ts'],
                match_score=s['match_score'],
                direction_bias=s['direction_bias'],
                tf=s['tf'],
                price_at_signal=s['price_at_signal'],
                path=path,
            )
            count += 1
        except Exception as e:
            print(f'  [LiveDetector] Failed to save signal: {e}')
    return count
