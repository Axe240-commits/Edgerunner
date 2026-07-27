#!/usr/bin/env python3
"""Tests for funding_loader hardening + flip funding.db guards (no network)."""
import io
import contextlib
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import funding_loader as fl
import backtest_flip


def _page(start_ts, n):
    return [{'fundingTime': start_ts + i * 8 * 3600_000,
             'fundingRate': '0.0001'} for i in range(n)]


class TestFundingLoader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)  # loader must create it fresh

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _run(self, pages):
        with mock.patch.object(fl, '_get_json', side_effect=pages), \
                mock.patch.object(fl.time, 'sleep'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(buf):
                rc = fl.main(['--db', self.tmp.name, '--since', '2025-01-01',
                              '--until', '2025-02-01'])
        return rc, buf.getvalue()

    def test_empty_api_gives_clear_error_not_crash(self):
        rc, out = self._run([[], []])
        self.assertEqual(rc, 1)
        self.assertIn('0 funding rows', out)

    def test_permanent_fetch_error_aborts_visibly(self):
        rc, out = self._run([RuntimeError('API down')])
        self.assertEqual(rc, 1)
        self.assertIn('failed permanently', out)

    def test_empty_midrange_page_warns_but_keeps_data(self):
        pages = [_page(1_735_689_600_000, 5), []]  # data, then empty page
        rc, out = self._run(pages)
        self.assertEqual(rc, 0)
        self.assertIn('WARNING: empty funding page', out)
        conn = sqlite3.connect(self.tmp.name)
        n = conn.execute('SELECT COUNT(*) FROM funding').fetchone()[0]
        conn.close()
        self.assertEqual(n, 5)


class TestFlipFundingGuard(unittest.TestCase):
    """--select funding must fail loudly on missing/empty funding.db."""

    def test_missing_db_file_exits_clearly(self):
        with self.assertRaises(SystemExit) as ctx:
            backtest_flip.main(['--db', '/nonexistent/e.db', '--mode', 'diagnose',
                                '--select', 'funding',
                                '--funding-db', '/nonexistent/funding.db'])
        self.assertIn('funding.db', str(ctx.exception))

    def test_empty_db_exits_clearly(self):
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.execute('CREATE TABLE funding (ts_ms INTEGER PRIMARY KEY, rate REAL)')
        conn.commit()
        conn.close()
        try:
            with self.assertRaises(SystemExit) as ctx:
                backtest_flip.main(['--db', '/nonexistent/e.db', '--mode',
                                    'diagnose', '--select', 'funding',
                                    '--funding-db', tmp.name])
            self.assertIn('fehlt/leer', str(ctx.exception))
        finally:
            os.unlink(tmp.name)


if __name__ == '__main__':
    unittest.main()
