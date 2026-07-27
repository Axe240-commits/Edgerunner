#!/usr/bin/env python3
"""Tests for the evidence artifact (synthetic mini DB, no network)."""
import json
import os
import sqlite3
import tempfile
import unittest

from evidence import build_evidence, _sha256_file, _coverage, TF_MS

H4 = TF_MS['4h']


class TestEvidence(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.dir, 'test.db')
        conn = sqlite3.connect(self.db_path)
        conn.execute('CREATE TABLE candles_4h (timestamp INTEGER, close REAL)')
        # 10 candles (4h), then a gap of 6 intervals (5 missing candles)
        for i in range(10):
            conn.execute('INSERT INTO candles_4h VALUES (?,?)',
                         (i * H4, 100.0))
        for i in range(15, 20):
            conn.execute('INSERT INTO candles_4h VALUES (?,?)',
                         (i * H4, 100.0))
        conn.commit()
        conn.close()
        self.report = os.path.join(self.dir, 'report.json')
        with open(self.report, 'w') as f:
            json.dump({'mode': 'diagnose'}, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_hash_reproducible(self):
        a = _sha256_file(self.db_path)
        b = _sha256_file(self.db_path)
        self.assertEqual(a['sha256'], b['sha256'])
        self.assertEqual(a['method'], 'full')

    def test_gap_report(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cov = _coverage(conn, 'candles_4h', H4)
        finally:
            conn.close()
        self.assertEqual(cov['rows'], 15)
        self.assertEqual(cov['min_ts'], 0)
        self.assertEqual(cov['max_ts'], 19 * H4)
        self.assertEqual(cov['gaps_over_2x_interval'], 1)
        self.assertEqual(cov['biggest_gap_intervals'], 6.0)

    def test_evidence_file_fields(self):
        class Cfg:
            train_fraction = 0.65
        out = build_evidence(
            script='backtest_x.py', mode='diagnose',
            argv=['x.py', '--mode', 'diagnose'], cfg=Cfg(),
            db_path=self.db_path, result_path=self.report,
            tables=['candles_4h'], adapter_name='binance-futures',
            adapter_files=[__file__],
            window={'since': '2025-01-01', 'until': None},
            train_cutoff_ts=6 * H4, out_dir=self.dir)
        self.assertTrue(os.path.isfile(out))
        with open(out) as f:
            ev = json.load(f)
        for key in ('evidence_version', 'script', 'mode', 'argv', 'git_commit',
                    'data_source', 'db', 'window', 'coverage', 'result_file',
                    'result_sha256'):
            self.assertIn(key, ev)
        self.assertEqual(ev['evidence_version'], 1)
        self.assertEqual(ev['db']['sha256'], _sha256_file(self.db_path)['sha256'])
        self.assertEqual(ev['coverage']['candles_4h']['gaps_over_2x_interval'], 1)
        self.assertEqual(ev['window']['train_cutoff_ts'], 6 * H4)
        self.assertEqual(ev['window']['train_fraction'], 0.65)
        self.assertTrue(ev['result_sha256'])
        self.assertIn('test_evidence.py',
                      next(iter(ev['data_source']['adapter_sha256'].keys())))


if __name__ == '__main__':
    unittest.main()
