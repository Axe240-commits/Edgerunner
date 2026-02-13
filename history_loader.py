#!/usr/bin/env python3
"""
Edgerunner History Loader — Load Binance klines for all timeframes.

Usage:
    python3 history_loader.py --days 30                        # All TFs, 30 days
    python3 history_loader.py --days 30 --tf 1m 5m 15m         # Selected TFs
    python3 history_loader.py --start 2024-01-01 --tf 1d       # Daily only
    python3 history_loader.py --fill-gaps --tf 1m              # Fill gaps in 1m
    python3 history_loader.py --enrich-whales                  # Whale features
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from candle_analyzer import CandleAnalyzer
from db import (init_db, insert_candles, count_candles, get_ts_range,
                DEFAULT_DB_PATH, TIMEFRAMES)

BINANCE_KLINE_URL = 'https://api.binance.com/api/v3/klines'
MAX_CANDLES_PER_REQUEST = 1000
RATE_LIMIT_DELAY = 0.1

# Binance BTC start: 2017-08-17
BINANCE_BTC_START_MS = 1502928000000

# Binance interval strings (identical to our TF names)
BINANCE_INTERVALS = {
    '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
    '1h': '1h', '4h': '4h', '1d': '1d', '1w': '1w', '1M': '1M',
}

# Milliseconds per interval (for cursor advancement)
INTERVAL_MS = {
    '1m': 60_000, '5m': 300_000, '15m': 900_000, '30m': 1_800_000,
    '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000,
    '1w': 604_800_000, '1M': 2_592_000_000,
}

# Swing lookback per TF (smaller TFs need more lookback)
TF_LOOKBACK = {
    '1m': 5, '5m': 5, '15m': 3, '30m': 3,
    '1h': 3, '4h': 3, '1d': 2, '1w': 2, '1M': 2,
}


def fetch_klines(symbol, interval, start_ms, end_ms=None, limit=1000):
    """Fetch klines from Binance for any interval."""
    bi = BINANCE_INTERVALS.get(interval, interval)
    params = f'symbol={symbol}&interval={bi}&startTime={start_ms}&limit={limit}'
    if end_ms:
        params += f'&endTime={end_ms}'
    url = f'{BINANCE_KLINE_URL}?{params}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Edgerunner/1.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def parse_klines(raw_klines):
    """Convert raw Binance klines to candle dicts with delta."""
    candles = []
    for k in raw_klines:
        vol = float(k[5])
        taker_buy = float(k[9])
        candles.append({
            'timestamp': k[0],
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': vol,
            'delta': taker_buy * 2 - vol,
        })
    return candles


def load_history(symbol='BTCUSDT', start_date=None, end_date=None,
                 days=None, timeframes=None, db_path=DEFAULT_DB_PATH,
                 fill_gaps=False):
    """Load historical candles from Binance for multiple timeframes.

    Args:
        symbol: Trading pair
        start_date: Start date string (YYYY-MM-DD)
        end_date: End date string (YYYY-MM-DD)
        days: Load last N days
        timeframes: List of TFs to load (default: all 9)
        db_path: Path to SQLite database
        fill_gaps: Fill gaps in existing DB data
    """
    init_db(db_path)

    if timeframes is None:
        timeframes = list(TIMEFRAMES)

    now_ms = int(time.time() * 1000)

    print(f'\n  Edgerunner History Loader (Multi-TF)')
    print(f'  {"─" * 40}')
    print(f'  Symbol:     {symbol}')
    print(f'  Timeframes: {", ".join(timeframes)}')
    print(f'  DB:         {db_path}')
    print()

    for tf in timeframes:
        if tf not in BINANCE_INTERVALS:
            print(f'  [SKIP] Unknown interval: {tf}')
            continue

        # Determine time range per TF
        if fill_gaps:
            ts_range = get_ts_range(tf=tf, path=db_path)
            if not ts_range:
                print(f'  [{tf}] DB empty — use --start or --days')
                continue
            start_ms = ts_range[0]
            end_ms = ts_range[1]
        elif days:
            end_ms = now_ms
            start_ms = now_ms - (days * 24 * 60 * 60_000)
        elif start_date:
            start_ms = _date_to_ms(start_date)
            end_ms = _date_to_ms(end_date) if end_date else now_ms
        else:
            print('  Specify --start, --days, or --fill-gaps')
            return

        start_ms = max(start_ms, BINANCE_BTC_START_MS)
        ims = INTERVAL_MS.get(tf, 60_000)
        total_candles = (end_ms - start_ms) // ims
        total_requests = max(1, (total_candles + MAX_CANDLES_PER_REQUEST - 1) // MAX_CANDLES_PER_REQUEST)

        print(f'  [{tf:>3s}] {_ms_to_date(start_ms)} -> {_ms_to_date(end_ms)}  (~{total_candles:,} candles, ~{total_requests} req)')

        lookback = TF_LOOKBACK.get(tf, 5)
        analyzer = CandleAnalyzer(swing_lookback=lookback)

        current_ms = start_ms
        total_loaded = 0
        total_stored = 0
        request_count = 0
        batch_size = 5000
        raw_buffer = []
        t_start = time.time()

        while current_ms < end_ms:
            try:
                raw = fetch_klines(symbol, tf, current_ms, end_ms, MAX_CANDLES_PER_REQUEST)
                if not raw:
                    break

                candles = parse_klines(raw)
                raw_buffer.extend(candles)
                total_loaded += len(candles)
                request_count += 1

                # Move cursor past last candle
                current_ms = raw[-1][0] + ims

                # Analyze and store when buffer is large enough
                if len(raw_buffer) >= batch_size or current_ms >= end_ms:
                    features = analyzer.analyze_batch(raw_buffer)
                    insert_candles(features, tf=tf, path=db_path)
                    total_stored += len(features)

                    elapsed = time.time() - t_start
                    rate = total_loaded / elapsed if elapsed > 0 else 0
                    pct = min(100, (current_ms - start_ms) / max(1, end_ms - start_ms) * 100)

                    sys.stdout.write(
                        f'\r  [{tf:>3s}] [{pct:5.1f}%] {total_loaded:>9,} loaded, '
                        f'{total_stored:>9,} stored | '
                        f'{rate:,.0f}/sec | {_ms_to_date(current_ms)}'
                    )
                    sys.stdout.flush()

                    # Keep last 200 candles for context continuity
                    raw_buffer = raw_buffer[-200:]

                time.sleep(RATE_LIMIT_DELAY)

            except Exception as e:
                print(f'\n  [{tf}] Error at {_ms_to_date(current_ms)}: {e}')
                time.sleep(2)
                continue

        elapsed = time.time() - t_start
        n_db = count_candles(tf=tf, path=db_path)
        print(f'\n  [{tf:>3s}] Done: {total_loaded:,} loaded in {elapsed:.1f}s, DB total: {n_db:,}')
        print()


def enrich_whale_features(tf='1m', db_path=DEFAULT_DB_PATH,
                          shadow_db='/home/albert/shadow-tracker/shadow_tracker.db'):
    """Enrich whale features from Shadow Tracker DB for overlapping timestamps."""
    import sqlite3
    from db import _table_name

    if not os.path.isfile(shadow_db):
        print(f'Shadow Tracker DB not found: {shadow_db}')
        return

    table = _table_name(tf)
    print(f'Enriching whale features in {table} from {shadow_db}...')

    conn_shadow = sqlite3.connect(shadow_db)
    conn_shadow.row_factory = sqlite3.Row

    tables = [r[0] for r in conn_shadow.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if 'whale_activity' not in tables:
        print('  No whale_activity table found')
        conn_shadow.close()
        return

    rows = conn_shadow.execute('''
        SELECT timestamp, side, is_new_position, notional_usd,
               COALESCE(
                   (SELECT follow_worthiness FROM wallet_profiles wp
                    WHERE wp.address = wa.wallet_address), 'UNPROVEN'
               ) as tier
        FROM whale_activity wa
        WHERE coin = 'BTC'
        ORDER BY timestamp
    ''').fetchall()
    conn_shadow.close()

    if not rows:
        print('  No whale activity data found')
        return

    print(f'  Found {len(rows)} whale signals')

    from collections import defaultdict
    minute_signals = defaultdict(list)
    for r in rows:
        minute_ts = int(r['timestamp']) // 60 * 60 * 1000
        minute_signals[minute_ts].append(dict(r))

    conn = sqlite3.connect(db_path)
    updated = 0

    tier_weights = {'ELITE': 5.0, 'PROVEN': 3.0, 'TRUSTED': 2.0,
                    'NEUTRAL': 1.0, 'UNPROVEN': 0.5, 'AVOID': 0.1}

    for minute_ts, signals in minute_signals.items():
        bull_p = 0.0
        bear_p = 0.0
        elite_active = 0

        for sig in signals:
            w = tier_weights.get(sig['tier'], 1.0)
            usd = sig.get('notional_usd', 0) or 0
            vol_w = max(0.1, (usd / 100000)) if usd > 0 else 0.1
            weight = w * vol_w

            if sig['tier'] in ('ELITE', 'PROVEN'):
                elite_active = 1

            if sig['side'] == 'BUY' and sig['is_new_position']:
                bull_p += weight
            elif sig['side'] == 'SELL' and sig['is_new_position']:
                bear_p += weight

        total = bull_p + bear_p
        if total > 0:
            sentiment = (bull_p - bear_p) / total
            confidence = min(1.0, len(signals) / 10.0)
            bull_norm = bull_p / total
            bear_norm = bear_p / total
            cluster = 1 if len(signals) >= 2 else 0
            cluster_str = min(1.0, len(signals) / 5.0) if cluster else 0.0
            cluster_dir = 1 if bull_p > bear_p else (-1 if bear_p > bull_p else 0)

            conn.execute(f'''
                UPDATE {table} SET
                    whale_sentiment=?, whale_confidence=?,
                    bull_pressure=?, bear_pressure=?,
                    whale_cluster=?, whale_cluster_strength=?,
                    whale_cluster_dir=?, elite_whale_active=?
                WHERE timestamp = ?
            ''', (sentiment, confidence, bull_norm, bear_norm,
                  cluster, cluster_str, cluster_dir, elite_active, minute_ts))
            updated += 1

    conn.commit()
    conn.close()
    print(f'  Updated {updated} candles with whale features')


def _date_to_ms(date_str):
    """Convert YYYY-MM-DD to milliseconds timestamp."""
    dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _ms_to_date(ms):
    """Convert ms timestamp to readable date string."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Edgerunner History Loader (Multi-TF)')
    parser.add_argument('--symbol', default='BTCUSDT', help='Trading pair')
    parser.add_argument('--start', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', help='End date (YYYY-MM-DD)')
    parser.add_argument('--days', type=int, help='Load last N days')
    parser.add_argument('--tf', nargs='*', default=None,
                        help='Timeframes to load (default: all). E.g. --tf 1m 5m 15m')
    parser.add_argument('--db', default=DEFAULT_DB_PATH, help='Database path')
    parser.add_argument('--fill-gaps', action='store_true', help='Fill gaps in existing data')
    parser.add_argument('--enrich-whales', action='store_true', help='Enrich whale features')
    args = parser.parse_args()

    if args.enrich_whales:
        enrich_whale_features(tf=args.tf[0] if args.tf else '1m', db_path=args.db)
    else:
        load_history(
            symbol=args.symbol,
            start_date=args.start,
            end_date=args.end,
            days=args.days,
            timeframes=args.tf,
            db_path=args.db,
            fill_gaps=args.fill_gaps,
        )
