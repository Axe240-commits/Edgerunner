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


def _http_error(code, retry_after=None):
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs['Retry-After'] = str(retry_after)
    return urllib.error.HTTPError('http://test', code, 'err', hdrs, None)


def _candle_dicts(start_ts, n, ims=60_000):
    """Binance-futures-shaped candle dicts for loader mocks."""
    return [{'timestamp': start_ts + i * ims, 'open': 1.0, 'high': 2.0,
             'low': 0.5, 'close': 1.5, 'volume': 10.0, 'delta': 1.0,
             'futures_volume': 10.0, 'futures_delta': 1.0, 'num_trades': 5}
            for i in range(n)]


class TestPartialBuckets(unittest.TestCase):
    """Incomplete derived 10m buckets (only one 5m candle) must be dropped,
    on BOTH aggregation paths (binance-futures and hyperliquid)."""

    START = 1_600_000_200_000  # 10m-aligned
    END = START + 30 * 60_000

    def test_binance_partial_bucket_dropped(self):
        # 5m klines: two full 10m buckets + ONE trailing 5m (half bucket).
        raw = [_kline(self.START + i * 300_000, 300_000) for i in range(5)]
        with mock.patch.object(api, '_binance_get_json', return_value=raw), \
                mock.patch.object(api.time, 'sleep'):
            candles = api.fetch_binance_futures_candles(
                '10m', start_ms=self.START, end_ms=self.END, limit=1000)
        self.assertEqual([c['timestamp'] for c in candles],
                         [self.START, self.START + 600_000])

    def test_hl_partial_bucket_dropped(self):
        hl_rows = [{'t': self.START + i * 300_000, 'o': '100', 'h': '110',
                    'l': '90', 'c': '105', 'v': '10'} for i in range(5)]
        with mock.patch('urllib.request.urlopen',
                        return_value=_FakeResp(hl_rows)):
            candles = api.fetch_candles('BTC', '10m', start_ms=self.START,
                                        end_ms=self.END, limit=500)
        self.assertEqual([c['timestamp'] for c in candles],
                         [self.START, self.START + 600_000])

    def test_bucket_cut_by_window_end_dropped(self):
        # "9-minute window": the 10m bucket [START, START+10m) extends past
        # end_ms (START+9m) -> dropped even though both 5m candles exist.
        raw = [_kline(self.START, 300_000), _kline(self.START + 300_000, 300_000)]
        with mock.patch.object(api, '_binance_get_json', return_value=raw), \
                mock.patch.object(api.time, 'sleep'):
            candles = api.fetch_binance_futures_candles(
                '10m', start_ms=self.START, end_ms=self.START + 540_000,
                limit=1000)
        self.assertEqual(candles, [])

    def test_base_grid_gap_drops_bucket(self):
        # Missing middle 5m timestamp: bucket [START,+10m) has only ONE of
        # the two EXPECTED base timestamps -> dropped; the full bucket stays.
        raw = [_kline(self.START, 300_000),
               _kline(self.START + 600_000, 300_000),
               _kline(self.START + 900_000, 300_000)]
        with mock.patch.object(api, '_binance_get_json', return_value=raw), \
                mock.patch.object(api.time, 'sleep'):
            candles = api.fetch_binance_futures_candles(
                '10m', start_ms=self.START, end_ms=self.END, limit=1000)
        self.assertEqual([c['timestamp'] for c in candles],
                         [self.START + 600_000])


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


class TestMidRangeEmpty(LoaderPatchMixin, unittest.TestCase):
    """Empty batch mid-range -> retry/abort path -> TF failed + INCOMPLETE.
    Empty batch at the right edge -> complete."""

    def _run_loader(self, side_effect, end, live_open_candle=False):
        m_fetch = mock.Mock(side_effect=side_effect)
        with mock.patch.multiple(hl, **self._loader_patches()), \
                mock.patch.object(hl, 'fetch_binance_futures_candles', m_fetch), \
                mock.patch('time.sleep'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                hl.load_history(start_date='2025-01-20', end_date=end,
                                timeframes=['1m'], db_path='/nonexistent/x.db',
                                source='binance-futures',
                                live_open_candle=live_open_candle)
        return buf.getvalue(), m_fetch

    def test_midrange_empty_marks_failed_incomplete(self):
        start = hl._date_to_ms('2025-01-20')
        out, m_fetch = self._run_loader(
            [_candle_dicts(start, 1000), [], [], [], [], []], '2025-01-23')
        self.assertIn('ABORT', out)
        self.assertIn('FAILED', out)
        self.assertIn('INCOMPLETE', out)
        self.assertEqual(m_fetch.call_count, 1 + hl.MAX_CONSECUTIVE_ERRORS)

    def test_empty_at_right_edge_is_complete(self):
        start = hl._date_to_ms('2025-01-20')
        # end = start + 2160min: after two full 1000-candle windows the last
        # window (160min) comes back empty -> true end of data -> complete.
        out, _ = self._run_loader(
            [_candle_dicts(start, 1000),
             _candle_dicts(start + 1000 * 60_000, 1000), []],
            '2025-01-21')
        self.assertNotIn('FAILED', out)
        self.assertIn('(complete)', out)


    def test_empty_at_right_edge_is_complete(self):
        start = hl._date_to_ms('2025-01-20')
        # end = start + 2160min: after two full 1000-candle windows the last
        # window (160min) comes back empty -> true end of data -> complete.
        out, _ = self._run_loader(
            [_candle_dicts(start, 1000),
             _candle_dicts(start + 1000 * 60_000, 1000), []],
            '2025-01-21')
        self.assertNotIn('FAILED', out)
        self.assertIn('(complete)', out)

    def test_source_ending_early_is_incomplete_not_failed(self):
        start = hl._date_to_ms('2025-01-20')
        # 4000 candles, then the last window (320min of 4320) comes back
        # empty: the source ends BEFORE the requested end -> the TF is
        # marked INCOMPLETE (separate list), but NOT hard-failed.
        out, _ = self._run_loader(
            [_candle_dicts(start, 1000),
             _candle_dicts(start + 1000 * 60_000, 1000),
             _candle_dicts(start + 2000 * 60_000, 1000),
             _candle_dicts(start + 3000 * 60_000, 1000), []],
            '2025-01-23')
        self.assertIn('INCOMPLETE (source ended before requested end)', out)
        self.assertIn('INCOMPLETE: 1m (source ended before requested end)', out)
        self.assertNotIn('FAILED', out)

    def test_running_candle_empty_needs_live_flag(self):
        start = hl._date_to_ms('2025-01-20')
        batches = [_candle_dicts(start, 1000),
                   _candle_dicts(start + 1000 * 60_000, 1000),
                   _candle_dicts(start + 2000 * 60_000, 1000),
                   _candle_dicts(start + 3000 * 60_000, 1000),
                   _candle_dicts(start + 4000 * 60_000, 319), []]
        # Without --live-open-candle: even an empty batch at the running
        # candle is INCOMPLETE before an explicit historical end.
        out, _ = self._run_loader(batches, '2025-01-23')
        self.assertIn('INCOMPLETE (source ended before requested end)', out)
        # With the flag: only the running candle is missing -> complete,
        # and the watermark shows the source's data level (gap 0 intervals).
        out, _ = self._run_loader(batches, '2025-01-23', live_open_candle=True)
        self.assertIn('(complete)', out)
        self.assertNotIn('INCOMPLETE', out)
        self.assertNotIn('FAILED', out)
        self.assertIn('watermark', out)
        # last delivered candle = start+4318min; only the running candle at
        # [4319, 4320) is missing -> gap exactly 1 interval.
        self.assertIn('end - 1 intervals', out)


class TestBaseDedup(unittest.TestCase):
    """Duplicated base-candle timestamps must not double-count bucket volume."""

    START = 1_600_000_200_000  # 10m-aligned
    END = START + 30 * 60_000

    def test_binance_dedup_no_double_volume(self):
        raw = [_kline(self.START + i * 300_000, 300_000) for i in range(4)]
        raw.insert(1, list(raw[1]))  # duplicate of the 2nd 5m kline
        with mock.patch.object(api, '_binance_get_json', return_value=raw), \
                mock.patch.object(api.time, 'sleep'):
            candles = api.fetch_binance_futures_candles(
                '10m', start_ms=self.START, end_ms=self.END, limit=1000)
        self.assertEqual(len(candles), 2)
        for c in candles:
            self.assertEqual(c['volume'], 20.0)  # 2 x 10, not 30

    def test_hl_dedup_no_double_volume(self):
        hl_rows = [{'t': self.START + i * 300_000, 'o': '100', 'h': '110',
                    'l': '90', 'c': '105', 'v': '10'} for i in range(4)]
        hl_rows.insert(1, dict(hl_rows[1]))  # duplicate timestamp
        with mock.patch('urllib.request.urlopen',
                        return_value=_FakeResp(hl_rows)):
            candles = api.fetch_candles('BTC', '10m', start_ms=self.START,
                                        end_ms=self.END, limit=500)
        self.assertEqual(len(candles), 2)
        for c in candles:
            self.assertEqual(c['volume'], 20.0)


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
