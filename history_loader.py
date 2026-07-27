#!/usr/bin/env python3
"""
Edgerunner History Loader — Hyperliquid OHLCV + Binance Volume/Delta.

OHLCV from Hyperliquid (where you trade), Volume+Delta from Binance (real delta).

Usage:
    python3 history_loader.py --days 7                         # All TFs, 7 days
    python3 history_loader.py --days 7 --tf 1m 5m 15m          # Selected TFs
    python3 history_loader.py --start 2026-02-01 --tf 1d       # Daily only
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
from hyperliquid_api import (fetch_candles as hl_fetch, fetch_binance_volume,
                             fetch_binance_futures_volume,
                             fetch_binance_futures_candles,
                             merge_binance_volume, INTERVAL_MS)

HL_MAX_PER_REQUEST = 500
BINANCE_MAX_PER_REQUEST = 1000
RATE_LIMIT_DELAY = 0.08
# Abort a timeframe after this many consecutive errors at the same cursor.
MAX_CONSECUTIVE_ERRORS = 5

# Hyperliquid BTC perps started ~2023-04
HL_BTC_START_MS = 1680307200000

# Binance BTCUSDT perpetual started 2019-09-08 — verified live against
# fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1d&startTime=0,
# earliest kline open time = 1567900800000 (2019-09-08 00:00:00 UTC).
BINANCE_FUTURES_BTC_START_MS = 1567900800000

# Swing lookback per TF (smaller TFs need more lookback)
TF_LOOKBACK = {
    '1m': 5, '3m': 5, '5m': 5, '10m': 4, '15m': 3, '30m': 3,
    '1h': 3, '2h': 3, '4h': 3, '1d': 2, '1w': 2, '1M': 2,
}


def _fetch_hl_batch(coin, interval, start_ms, end_ms, limit=500):
    """Fetch one batch of candles from Hyperliquid."""
    return hl_fetch(coin, interval, start_ms=start_ms, end_ms=end_ms, limit=limit)


def _fetch_binance_delta_batch(interval, start_ms, end_ms, limit=1000):
    """Fetch Binance volume+delta for a time range.

    Returns (ts->data map, error-or-None). Errors are returned instead of
    being silently swallowed, so the caller can warn about spot-merge gaps.
    """
    try:
        return fetch_binance_volume(interval, start_ms=start_ms, end_ms=end_ms,
                                    limit=limit), None
    except Exception as e:
        return {}, e


def load_history(coin='BTC', start_date=None, end_date=None,
                 days=None, timeframes=None, db_path=DEFAULT_DB_PATH,
                 fill_gaps=False, source='hyperliquid',
                 live_open_candle=False):
    """Load historical candles from Hyperliquid + Binance delta.

    source='hyperliquid':     OHLCV from Hyperliquid (matches your trading
                              charts), volume+delta merged from Binance
                              spot/futures (real taker buy/sell data).
    source='binance-futures': OHLCV + native futures volume/delta from Binance
                              Futures BTCUSDT perp (full history, unlike the
                              HL candleSnapshot for small TFs). The Binance
                              spot merge (spot_volume/spot_delta) is kept.
    live_open_candle:         only with this flag an empty batch at the very
                              end of the range may count as "complete" (the
                              still-running, not-yet-closed candle). Without
                              it an empty batch before an explicit end is
                              always marked INCOMPLETE.
    """
    init_db(db_path)

    if timeframes is None:
        timeframes = list(TIMEFRAMES)

    now_ms = int(time.time() * 1000)
    # Candles per OHLCV request: HL caps at 500, Binance at 1000.
    per_request = (BINANCE_MAX_PER_REQUEST if source == 'binance-futures'
                   else HL_MAX_PER_REQUEST)
    # History floor is source-specific: HL perps started 2023-04, the
    # Binance BTCUSDT perp already in 2019-09.
    source_start_floor = (BINANCE_FUTURES_BTC_START_MS
                          if source == 'binance-futures' else HL_BTC_START_MS)

    ok_tfs = []
    failed_tfs = []
    incomplete_tfs = []

    print(f'\n  Edgerunner History Loader')
    print(f'  {"─" * 40}')
    print(f'  Source:     {source}')
    if source == 'binance-futures':
        print(f'  OHLCV:      Binance Futures (BTCUSDT perp)')
        print(f'  Vol/Delta:  native futures taker delta + Binance Spot merge')
    else:
        print(f'  OHLCV:      Hyperliquid ({coin})')
        print(f'  Vol/Delta:  Binance (BTCUSDT Spot)')
    print(f'  Timeframes: {", ".join(timeframes)}')
    print(f'  DB:         {db_path}')
    print()

    for tf in timeframes:
        ims = INTERVAL_MS.get(tf)
        if not ims:
            print(f'  [SKIP] Unknown interval: {tf}')
            continue

        # Determine time range
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

        start_ms = max(start_ms, source_start_floor)
        total_candles = (end_ms - start_ms) // ims
        n_requests = max(1, (total_candles + per_request - 1) // per_request)

        print(f'  [{tf:>3s}] {_ms_to_date(start_ms)} -> {_ms_to_date(end_ms)}  (~{total_candles:,} candles, ~{n_requests} {source} req)')

        lookback = TF_LOOKBACK.get(tf, 2)
        analyzer = CandleAnalyzer(swing_lookback=lookback)

        current_ms = start_ms
        total_loaded = 0
        total_stored = 0
        batch_size = 5000  # analyze+store every N candles
        raw_buffer = []
        t_start = time.time()
        consecutive_errors = 0
        last_error_cursor = None
        tf_failed = False
        tf_incomplete = False
        tf_incomplete_reason = None
        last_data_ts = None   # watermark: newest candle ts the source delivered
        spot_missing_windows = 0

        while current_ms < end_ms:
            try:
                # 1) Fetch OHLCV from the chosen source.
                #    HL returns the LAST N candles in a window, so the end is
                #    capped to a sliding window of max N candles either way.
                batch_end = min(end_ms, current_ms + per_request * ims)
                if source == 'binance-futures':
                    candles = fetch_binance_futures_candles(
                        tf, start_ms=current_ms, end_ms=batch_end,
                        limit=per_request)
                else:
                    candles = _fetch_hl_batch(coin, tf, current_ms, batch_end,
                                              HL_MAX_PER_REQUEST)
                if not candles:
                    remaining = end_ms - current_ms
                    if remaining > per_request * ims:
                        # Empty MID-RANGE batch (API hiccup, not the true
                        # end of data): feed the retry/abort path instead
                        # of silently accepting a partial timeframe.
                        if last_error_cursor == current_ms:
                            consecutive_errors += 1
                        else:
                            consecutive_errors = 1
                            last_error_cursor = current_ms
                        print(f'\n  [{tf}] WARNING: empty mid-range batch at '
                              f'{_ms_to_date(current_ms)} '
                              f'(attempt {consecutive_errors})')
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            print(f'  [{tf:>3s}] ABORT after '
                                  f'{consecutive_errors} empty mid-range '
                                  f'batches at {_ms_to_date(current_ms)}')
                            tf_failed = True
                            break
                        time.sleep(2)
                        continue
                    # Right-edge empty batch. Documented rule: an empty
                    # batch before an EXPLICIT historical end is INCOMPLETE
                    # by default — the source stopped before the requested
                    # end. The relaxed reading ("only the still-running,
                    # not-yet-closed candle is missing -> complete") applies
                    # ONLY with --live-open-candle enabled, and then also
                    # requires data in the previous batch plus the cursor
                    # at the last full interval (end_ms - ims).
                    last_full = end_ms - ims
                    if (live_open_candle and total_loaded > 0
                            and current_ms >= last_full):
                        break  # running candle only, live mode
                    reason = ('source ended early'
                              if live_open_candle else
                              'source ended before requested end')
                    print(f'\n  [{tf}] WARNING: {reason} at '
                          f'{_ms_to_date(current_ms)} '
                          f'({total_loaded:,}/{total_candles:,}) — INCOMPLETE')
                    tf_incomplete = True
                    tf_incomplete_reason = reason
                    break

                # 2) Fetch Binance SPOT volume+delta for same range and merge.
                #    Failures leave a spot gap — warn throttled, count for the
                #    TF-end summary instead of swallowing silently.
                bv, spot_err = _fetch_binance_delta_batch(
                    tf,
                    start_ms=candles[0]['timestamp'],
                    end_ms=candles[-1]['timestamp'] + ims,
                    limit=BINANCE_MAX_PER_REQUEST,
                )
                if spot_err is not None:
                    spot_missing_windows += 1
                    if spot_missing_windows <= 3:
                        print(f'\n  [{tf}] WARNING: spot-merge failed for '
                              f'window at {_ms_to_date(current_ms)}: {spot_err}')

                if source == 'binance-futures':
                    # Futures volume/delta is already native on the candles;
                    # merge spot manually. merge_binance_volume would
                    # overwrite the legacy volume/delta fields with spot data.
                    if bv:
                        for c in candles:
                            sv = bv.get(c['timestamp'])
                            if sv:
                                c['spot_volume'] = sv['volume']
                                c['spot_delta'] = sv['delta']
                                c['futures_minus_spot_volume'] = c['volume'] - sv['volume']
                                c['futures_minus_spot_delta'] = c['delta'] - sv['delta']
                else:
                    fv = {}
                    try:
                        fv = fetch_binance_futures_volume(
                            tf,
                            start_ms=candles[0]['timestamp'],
                            end_ms=candles[-1]['timestamp'] + ims,
                            limit=BINANCE_MAX_PER_REQUEST,
                        )
                    except Exception:
                        fv = {}

                    if bv or fv:
                        merge_binance_volume(candles, bv, fv)

                raw_buffer.extend(candles)
                total_loaded += len(candles)
                last_data_ts = candles[-1]['timestamp']

                # Move cursor past last candle
                current_ms = candles[-1]['timestamp'] + ims
                consecutive_errors = 0  # progress made — reset error streak

                # Analyze and store when buffer is large enough
                if len(raw_buffer) >= batch_size or current_ms >= end_ms:
                    features = analyzer.analyze_batch(raw_buffer)

                    # Carry spot/futures split metrics from raw candles into feature rows
                    raw_by_ts = {c.get('timestamp'): c for c in raw_buffer}
                    for f in features:
                        src = raw_by_ts.get(f.get('timestamp'))
                        if not src:
                            continue
                        for k in (
                            'spot_volume', 'spot_delta',
                            'futures_volume', 'futures_delta',
                            'futures_minus_spot_volume', 'futures_minus_spot_delta',
                        ):
                            if k in src:
                                f[k] = src.get(k)

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

                    # Keep last 500 candles for seeker cycle continuity
                    raw_buffer = raw_buffer[-500:]

                time.sleep(RATE_LIMIT_DELAY)

            except Exception as e:
                # Retry the same cursor, but not forever: a permanent failure
                # (e.g. API down) must abort visibly instead of looping
                # endlessly on the same position.
                if last_error_cursor == current_ms:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 1
                    last_error_cursor = current_ms
                print(f'\n  [{tf}] Error at {_ms_to_date(current_ms)}: {e}')
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f'  [{tf:>3s}] ABORT after {consecutive_errors} '
                          f'consecutive errors at {_ms_to_date(current_ms)}: {e}')
                    tf_failed = True
                    break
                time.sleep(2)
                continue

        elapsed = time.time() - t_start
        n_db = count_candles(tf=tf, path=db_path)
        if spot_missing_windows:
            print(f'\n  [{tf:>3s}] spot-merge missing for {spot_missing_windows} windows')
        if total_loaded == 0 and not tf_failed:
            print(f'\n  [{tf:>3s}] WARNING: 0 candles loaded — source has no data for this range')
        # complete = the loader reached the requested end (live mode: the
        # last full interval before the running candle). INCOMPLETE = source
        # ended before the requested end (-> incomplete_tfs) or aborted
        # (-> failed_tfs). The watermark states how far the source actually
        # delivered data (last candle ts vs end_ms, in intervals).
        if tf_failed:
            status = 'INCOMPLETE (aborted)'
        elif tf_incomplete:
            status = f'INCOMPLETE ({tf_incomplete_reason})'
        else:
            status = 'complete'
        if last_data_ts is not None:
            gap = max(0, int((end_ms - (last_data_ts + ims)) // ims))
            watermark = (f'watermark {_ms_to_date(last_data_ts)} '
                         f'(end - {gap} intervals)')
        else:
            watermark = 'watermark - (no data)'
        print(f'\n  [{tf:>3s}] Done: {total_loaded:,}/{total_candles:,} loaded '
              f'({status}) in {elapsed:.1f}s, DB total: {n_db:,} | {watermark}')
        print()

        if tf_failed:
            failed_tfs.append(tf)
        elif tf_incomplete:
            incomplete_tfs.append((tf, tf_incomplete_reason))
        else:
            ok_tfs.append(tf)

    # Final summary: which timeframes completed, which are suspect, which failed.
    print(f'  Summary: {len(ok_tfs)} TF(s) ok ({", ".join(ok_tfs) or "-"})')
    if incomplete_tfs:
        print('  INCOMPLETE: ' + ', '.join(
            f'{tf} ({reason})' for tf, reason in incomplete_tfs))
    if failed_tfs:
        print(f'  FAILED: {", ".join(failed_tfs)}')


def _resolve_shadow_db_path(explicit_path=None):
    """Resolve a usable Shadow Tracker DB path."""
    candidates = [
        explicit_path,
        os.environ.get('SHADOW_DB_PATH'),
        '/home/axe240/Projects/recovery/tokyo_full_repos_2026-03-03/shadow_tracker/shadow_tracker.db',
        '/home/axe240/Projects/recovery/tokyo_repos_2026-03-03/shadow_tracker/shadow_tracker.db',
        '/home/axe240/shadow-tracker/shadow_tracker.db',
        '/home/albert/shadow-tracker/shadow_tracker.db',
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _tf_to_ms(tf: str) -> int:
    mapping = {
        '1m': 60_000,
        '3m': 180_000,
        '5m': 300_000,
        '10m': 600_000,
        '15m': 900_000,
        '30m': 1_800_000,
        '1h': 3_600_000,
        '2h': 7_200_000,
        '4h': 14_400_000,
        '1d': 86_400_000,
        '1w': 604_800_000,
        '1M': 2_592_000_000,
    }
    return mapping.get(tf, 60_000)


def enrich_whale_features(tf='1m', db_path=DEFAULT_DB_PATH, shadow_db=None):
    """Enrich whale features from Shadow Tracker DB for overlapping timestamps."""
    import sqlite3
    from collections import defaultdict
    from db import _table_name

    shadow_db = _resolve_shadow_db_path(shadow_db)
    if not shadow_db:
        print('Shadow Tracker DB not found in known locations')
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

    tf_ms = _tf_to_ms(tf)
    bucket_signals = defaultdict(list)
    for r in rows:
        event_ms = int(float(r['timestamp']) * 1000)
        bucket_ts = (event_ms // tf_ms) * tf_ms
        bucket_signals[bucket_ts].append(dict(r))

    conn = sqlite3.connect(db_path)
    updated = 0

    tier_weights = {'ELITE': 5.0, 'PROVEN': 3.0, 'TRUSTED': 2.0,
                    'NEUTRAL': 1.0, 'UNPROVEN': 0.5, 'AVOID': 0.1}

    for candle_ts, signals in bucket_signals.items():
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
                  cluster, cluster_str, cluster_dir, elite_active, candle_ts))
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
    parser = argparse.ArgumentParser(description='Edgerunner History Loader (HL + Binance Delta)')
    parser.add_argument('--coin', default='BTC', help='Hyperliquid coin (default: BTC)')
    parser.add_argument('--start', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', help='End date (YYYY-MM-DD)')
    parser.add_argument('--days', type=int, help='Load last N days')
    parser.add_argument('--tf', nargs='*', default=None,
                        help='Timeframes to load (default: all). E.g. --tf 1m 5m 15m')
    parser.add_argument('--db', default=DEFAULT_DB_PATH, help='Database path')
    parser.add_argument('--source', choices=['hyperliquid', 'binance-futures'],
                        default='hyperliquid',
                        help='OHLCV source (default: hyperliquid). '
                             'binance-futures: full history + native futures '
                             'volume/delta from Binance Futures BTCUSDT perp')
    parser.add_argument('--fill-gaps', action='store_true', help='Fill gaps in existing data')
    parser.add_argument('--live-open-candle', action='store_true',
                        help='live mode: accept the still-running, not-yet-'
                             'closed candle as the expected range end (empty '
                             'final batch then counts as complete)')
    parser.add_argument('--enrich-whales', action='store_true', help='Enrich whale features')
    args = parser.parse_args()

    if args.enrich_whales:
        enrich_whale_features(tf=args.tf[0] if args.tf else '1m', db_path=args.db)
    else:
        load_history(
            coin=args.coin,
            start_date=args.start,
            end_date=args.end,
            days=args.days,
            timeframes=args.tf,
            db_path=args.db,
            fill_gaps=args.fill_gaps,
            source=args.source,
            live_open_candle=args.live_open_candle,
        )
