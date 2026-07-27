#!/usr/bin/env python3
"""Unit tests for backtest_seeker (synthetic candles, no DB/network)."""
import unittest

from backtest_breaker import Config, simulate_trade, H1_MS
from backtest_seeker import run_seeker_kills

H4_MS = 4 * H1_MS
T0 = 1_700_000_000_000


def mk(ts, **kw):
    c = {
        'timestamp': ts, 'open': 100.0, 'high': 100.5, 'low': 99.5,
        'close': 100.0, 'volume': 100.0, 'delta': 0.0, 'delta_pct': 0.0,
        'vol_vs_ma': 1.0, 'atr14': 1.0,
        'is_seeker_hs': 0, 'is_seeker_ls': 0, 'is_seeker_kill': 0,
        'killed_seeker_ts': 0, 'killed_seeker_divs': 0,
        'killed_seekers_count': 0,
        'seeker_zone_top': 0.0, 'seeker_zone_bottom': 0.0,
        'is_swing_high': 0, 'is_swing_low': 0,
    }
    c.update(kw)
    return c


def make_setup(n=30):
    return [mk(T0 + i * H4_MS, close=97.0) for i in range(n)]


def flat_exec(start_ts, n_bars, close=101.2, high=101.4, low=101.0, **kw):
    return [mk(start_ts + k * H1_MS, close=close, high=high, low=low, **kw)
            for k in range(n_bars)]


class TestKilledHsShort(unittest.TestCase):
    """Seeker HS killed (close above sweep high) -> retest -> short win."""

    def _build(self):
        h4 = make_setup()
        h4[1].update(is_seeker_hs=1, is_swing_high=1,
                     open=100.5, high=101.0, low=98.5, close=99.5)
        h4[3].update(is_seeker_kill=1, killed_seeker_ts=h4[1]['timestamp'],
                     killed_seeker_divs=1, killed_seekers_count=1,
                     seeker_zone_top=101.0, seeker_zone_bottom=100.0,
                     close=101.5, atr14=2.0)
        # exec 1h bars from the candle after the kill
        start = h4[4]['timestamp']
        ex = flat_exec(start, 100)
        ex[2]['low'] = 100.5          # retest: dips into the zone [100,101]
        ex[3]['low'] = 100.2
        ex[3]['close'] = 99.8         # closes out below zone bottom -> entry
        ex[4]['low'] = 96.0           # runs to 2R target (96.4)
        ex[4]['high'] = 100.0
        return h4, ex

    def test_retest_short_win(self):
        h4, ex = self._build()
        cfg = Config(setup_tf='4h', exec_tf='1h', validity_h1=24)
        setups = run_seeker_kills(h4, ex, cfg)
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s['direction'], 'short')
        self.assertEqual(s['outcome'], 'entered')
        self.assertAlmostEqual(s['entry_price'], 99.8)
        self.assertAlmostEqual(s['pullback_stop'], 101.5)  # 101 + 0.25*2
        t = simulate_trade(ex, s, 'pullback', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'target')
        self.assertGreater(t['r_net'], 0)

    def test_variant_b_entry_is_kill_close(self):
        h4, ex = self._build()
        # Kill closes at 101.2 -> stop 101.5 is ABOVE the short entry and
        # risk 0.3 >= 2x round-trip costs (4 x 0.0007 x 101.2 = 0.28)
        # -> protective, variant B executable.
        h4[3]['close'] = 101.2
        cfg = Config(setup_tf='4h', exec_tf='1h', validity_h1=24)
        s = run_seeker_kills(h4, ex, cfg)[0]
        self.assertIsNotNone(s['variant_b'])
        self.assertAlmostEqual(s['variant_b']['entry_price'], 101.2)

    def test_variant_b_skipped_when_stop_not_protective(self):
        h4, ex = self._build()
        # Kill closes at 101.4 -> stop 101.5, risk 0.1 < 0.5*ATR -> guard.
        h4[3]['close'] = 101.4
        cfg = Config(setup_tf='4h', exec_tf='1h', validity_h1=24)
        s = run_seeker_kills(h4, ex, cfg)[0]
        self.assertIsNone(s['variant_b'])
        self.assertEqual(s['variant_b_skip'], 'non_protective_stop')


class TestNoKillNoSetup(unittest.TestCase):
    def test_no_kill_no_setups(self):
        h4 = make_setup()
        h4[1].update(is_seeker_hs=1, open=100.5, high=101.0, close=99.5)
        ex = flat_exec(h4[1]['timestamp'], 100)
        cfg = Config(setup_tf='4h', exec_tf='1h', validity_h1=24)
        self.assertEqual(run_seeker_kills(h4, ex, cfg), [])


class TestKilledLsLongMirror(unittest.TestCase):
    def test_killed_ls_long_win(self):
        h4 = make_setup()
        h4[1].update(is_seeker_ls=1, is_swing_low=1,
                     open=99.5, high=101.0, low=99.0, close=100.5)
        h4[3].update(is_seeker_kill=1, killed_seeker_ts=h4[1]['timestamp'],
                     killed_seeker_divs=0, killed_seekers_count=1,
                     seeker_zone_top=100.0, seeker_zone_bottom=99.0,
                     close=98.5, atr14=2.0)
        start = h4[4]['timestamp']
        ex = flat_exec(start, 100, close=98.8, high=99.0, low=98.6)
        ex[2]['high'] = 99.5          # retest up into the zone [99,100]
        ex[3]['high'] = 100.1
        ex[3]['close'] = 100.3        # closes out above zone top -> long
        ex[4]['high'] = 104.0         # runs to 2R target (103.9)
        ex[4]['low'] = 100.0
        cfg = Config(setup_tf='4h', exec_tf='1h', validity_h1=24)
        setups = run_seeker_kills(h4, ex, cfg)
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s['direction'], 'long')
        self.assertEqual(s['outcome'], 'entered')
        self.assertAlmostEqual(s['entry_price'], 100.3)
        self.assertAlmostEqual(s['pullback_stop'], 98.5)  # 99 - 0.25*2
        t = simulate_trade(ex, s, 'pullback', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'target')
        self.assertGreater(t['r_net'], 0)


if __name__ == '__main__':
    unittest.main()
