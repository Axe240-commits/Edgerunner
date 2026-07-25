#!/usr/bin/env python3
"""
Funding rate backfill — Binance BTCUSDT perp funding history into a
SEPARATE small DB (funding.db). Never touches edgerunner.db.

Source: GET https://fapi.binance.com/fapi/v1/fundingRate
  ?symbol=BTCUSDT&startTime=...&endTime=...&limit=1000
Funding prints every 8h since 2019 — full history, no key needed.

Robustness: bounded retries, honors Retry-After on 429, idempotent
(INSERT OR IGNORE on ts_ms PK).

Usage:
    python3 funding_loader.py --db funding.db --since 2025-01-01
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

FUNDING_URL = 'https://fapi.binance.com/fapi/v1/fundingRate'
MAX_PER_REQUEST = 1000
RATE_LIMIT_DELAY = 0.15


def _get_json(url, retries=5):
    """GET with timeout + backoff; honors Retry-After on 429."""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Edgerunner/1.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 or e.code >= 500:
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
    raise RuntimeError(f'funding request failed after {retries} attempts: {last_err}')


def _date_to_ms(s):
    dt = datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def main(argv=None):
    p = argparse.ArgumentParser(description='Binance funding rate backfill')
    p.add_argument('--db', required=True, help='funding.db path (created)')
    p.add_argument('--since', default='2025-01-01', help='start date YYYY-MM-DD')
    p.add_argument('--until', help='end date YYYY-MM-DD (default: now)')
    args = p.parse_args(argv)

    start_ms = _date_to_ms(args.since)
    end_ms = _date_to_ms(args.until) if args.until else int(time.time() * 1000)

    conn = sqlite3.connect(args.db)
    conn.execute('CREATE TABLE IF NOT EXISTS funding ('
                 'ts_ms INTEGER PRIMARY KEY, rate REAL NOT NULL)')
    conn.commit()

    cursor = start_ms
    total = 0
    while cursor < end_ms:
        url = (f'{FUNDING_URL}?symbol=BTCUSDT&startTime={cursor}'
               f'&endTime={end_ms}&limit={MAX_PER_REQUEST}')
        try:
            rows = _get_json(url)
        except Exception as e:
            print(f'ERROR at {cursor}: {e}', file=sys.stderr)
            return 1
        if not rows:
            break
        for r in rows:
            conn.execute('INSERT OR IGNORE INTO funding (ts_ms, rate) VALUES (?,?)',
                         (int(r['fundingTime']), float(r['fundingRate'])))
        conn.commit()
        total += len(rows)
        last_ts = int(rows[-1]['fundingTime'])
        print(f'\r{total:,} funding prints ... '
              f'{datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc):%Y-%m-%d}',
              end='', flush=True)
        cursor = last_ts + 1
        time.sleep(RATE_LIMIT_DELAY)

    n, lo, hi = conn.execute(
        'SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM funding').fetchone()
    print(f'\nDone: {n:,} rows, '
          f'{datetime.fromtimestamp(lo / 1000, tz=timezone.utc):%Y-%m-%d} -> '
          f'{datetime.fromtimestamp(hi / 1000, tz=timezone.utc):%Y-%m-%d}')
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
