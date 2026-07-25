#!/usr/bin/env python3
"""Unit tests for the backfill boundary + retry fixes (no network).

Covers:
1. Source-specific start floor (P1-1)
2. Strict half-open end boundary [start_ms, end_ms) (P1-2)
3. Retry/abort behavior (Retry-After, backoff, loader error streak)
"""
import contextlib
import email.message
import io
import json
import unittest
import urllib.error
from unittest import mock

import hyperliquid_api as api
import history_loader as hl


def _kline(ts, interval_ms=60_000):
    """Build a Binance kline row: [open_time, o, h, l, c, volume, close_time,
    quote_vol, num_trades, taker_buy_base, taker_buy_quote, ignore]."""
    return [ts, '100', '110', '90', '105', '10', ts + interval_ms - 1,
            '1000', 7, '6', '600', '0']


class _FakeResp:
    """Minimal context-manager response for urllib.request.urlopen mocks."""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code, retry_after=None):
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs['Retry-After'] = str(retry_after)
    return urllib.error.HTTPError('http://test', code, 'err', hdrs, None)


class LoaderPatchMixin:
    """Common patches so load_history never touches DB, analysis or network."""

    def _loader_patches(self, **overrides):
        patches = {
            'init_db': mock.DEFAULT,
            'insert_candles': mock.DEFAULT,
            'count_candles': mock.Mock(return_value=0),
            'CandleAnalyzer': mock.DEFAULT,
            '_fetch_binance_delta_batch': mock.Mock(return_value=({}, None)),
        }
        patches.update(overrides)
        return patches


class TestStartFloor(LoaderPatchMixin, unittest.TestCase):
    """P1-1: the history floor must be source-specific."""

    def test_binance_start_not_clamped_to_hl_floor(self):
        # 2020-01-01 lies between the Binance floor (2019-09) and the HL
        # floor (2023-04): must stay untouched for binance-futures.
        m_fetch = mock.Mock(return_value=[])
        with mock.patch.multiple(hl, **self._loader_patches()), \
                mock.patch.object(hl, 'fetch_binance_futures_candles', m_fetch):
            with contextlib.redirect_stdout(io.StringIO()):
                hl.load_history(start_date='2020-01-01', end_date='2020-01-02',
                                timeframes=['1m'], db_path='/nonexistent/x.db',
                                source='binance-futures')
        kwargs = m_fetch.call_args.kwargs
        self.assertEqual(kwargs['start_ms'], hl._date_to_ms('2020-01-01'))
        self.assertLess(kwargs['start_ms'], hl.HL_BTC_START_MS)

    def test_hyperliquid_start_clamped_to_hl_floor(self):
        m_hl_fetch = mock.Mock(return_value=[])
        with mock.patch.multiple(hl, **self._loader_patches()), \
                mock.patch.object(hl, 'hl_fetch', m_hl_fetch):
            with contextlib.redirect_stdout(io.StringIO()):
                hl.load_history(start_date='2020-01-01', end_date='2023-05-01',
                                timeframes=['1m'], db_path='/nonexistent/x.db',
                                source='hyperliquid')
        kwargs = m_hl_fetch.call_args.kwargs
        self.assertEqual(kwargs['start_ms'], hl.HL_BTC_START_MS)


class TestEndBoundary(unittest.TestCase):
    """P1-2: Binance endTime is inclusive — results must be strictly < end."""

    START = 1_600_000_200_000  # 10m-aligned
    END = START + 30 * 60_000

    def test_native_interval_excludes_candle_at_end(self):
        # 1m klines covering [START, END] inclusive plus one beyond.
        raw = [_kline(self.START + i * 60_000) for i in range(0, 33)]
        with mock.patch.object(api, '_binance_get_json', return_value=raw):
            candles = api.fetch_binance_futures_candles(
                '1m', start_ms=self.START, end_ms=self.END, limit=1000)
        timestamps = [c['timestamp'] for c in candles]
        self.assertNotIn(self.END, timestamps)
        self.assertTrue(all(self.START <= ts < self.END for ts in timestamps))
        # The last valid candle before the boundary is present.
        self.assertIn(self.END - 60_000, timestamps)
        self.assertEqual(len(candles), 30)

    def test_aggregated_interval_excludes_bucket_at_end(self):
        # 10m aggregated from 5m: 5m klines covering [START, END] inclusive
        # plus one beyond. Buckets must also be strictly < END.
        raw = [_kline(self.START + i * 300_000, 300_000) for i in range(0, 8)]
        with mock.patch.object(api, '_binance_get_json', return_value=raw), \
                mock.patch.object(api.time, 'sleep'):
            candles = api.fetch_binance_futures_candles(
                '10m', start_ms=self.START, end_ms=self.END, limit=1000)
        timestamps = [c['timestamp'] for c in candles]
        self.assertTrue(all(ts % 600_000 == 0 for ts in timestamps))
        self.assertTrue(all(self.START <= ts < self.END for ts in timestamps))
        self.assertEqual(timestamps,
                         [self.START, self.START + 600_000,
                          self.START + 1_200_000])


class TestRetryBehavior(unittest.TestCase):
    """Robustness: Retry-After handling, backoff, final exception."""

    def test_retry_after_header_is_honored_then_raises(self):
        err = _http_error(429, retry_after=3)
        with mock.patch('urllib.request.urlopen', side_effect=err), \
                mock.patch.object(api.time, 'sleep') as m_sleep:
            with self.assertRaises(RuntimeError):
                api._binance_get_json('http://test', retries=3)
        self.assertEqual(m_sleep.call_count, 3)
        for call in m_sleep.call_args_list:
            self.assertEqual(call.args[0], 3.0)

    def test_linear_backoff_on_network_error(self):
        err = urllib.error.URLError('boom')
        with mock.patch('urllib.request.urlopen', side_effect=err), \
                mock.patch.object(api.time, 'sleep') as m_sleep:
            with self.assertRaises(RuntimeError):
                api._binance_get_json('http://test', retries=3)
        delays = [c.args[0] for c in m_sleep.call_args_list]
        self.assertEqual(delays, [1.0, 2.0, 3.0])

    def test_success_after_transient_429(self):
        err = _http_error(429, retry_after=1)
        ok = _FakeResp({'ok': True})
        with mock.patch('urllib.request.urlopen', side_effect=[err, ok]), \
                mock.patch.object(api.time, 'sleep'):
            self.assertEqual(api._binance_get_json('http://test'), {'ok': True})

    def test_loader_aborts_after_error_streak(self):
        """A permanently failing fetch must abort visibly, not loop forever."""
        m_fetch = mock.Mock(side_effect=RuntimeError('API down'))
        mixin = LoaderPatchMixin()
        with mock.patch.multiple(hl, **mixin._loader_patches()), \
                mock.patch.object(hl, 'fetch_binance_futures_candles', m_fetch), \
                mock.patch('time.sleep'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                hl.load_history(start_date='2025-01-20', end_date='2025-01-21',
                                timeframes=['1m'], db_path='/nonexistent/x.db',
                                source='binance-futures')
        out = buf.getvalue()
        self.assertIn('ABORT after', out)
        self.assertIn('FAILED', out)
        self.assertEqual(m_fetch.call_count, hl.MAX_CONSECUTIVE_ERRORS)


if __name__ == '__main__':
    unittest.main()
