#!/usr/bin/env python3
"""Unit tests for backtest_flip (synthetic candles, no DB/network)."""
import unittest

from backtest_breaker import Config, simulate_trade, H1_MS
from backtest_flip import run_flips

M15_MS = 900_000
T0 = 1_700_000_000_000


def mk(ts, **kw):
    c = {
        'timestamp': ts, 'open': 100.0, 'high': 100.5, 'low': 99.5,
        'close': 100.0, 'volume': 100.0, 'delta': 0.0, 'delta_pct': 0.0,
        'vol_vs_ma': 1.0, 'atr14': 1.0, 'bos_bull': 0, 'bos_bear': 0,
        'break_depth': 0.0, 'is_swing_high': 0, 'is_swing_low': 0,
    }
    c.update(kw)
    return c


def flat_m15(hour_start, hours, close=95.0, high=95.5, low=94.5, **kw):
    bars = []
    for h in range(hours):
        for q in range(4):
            bars.append(mk(hour_start + h * H1_MS + q * M15_MS,
                           close=close, high=high, low=low, **kw))
    return bars


def make_h1(n, breaker_idx=1, breaker=None, fill_close=95.0):
    h1 = [mk(T0 + i * H1_MS, close=fill_close) for i in range(n)]
    if breaker is not None:
        h1[breaker_idx] = breaker
    return h1


def bear_break(ts):
    """H1 closes under swing low: close 96, depth 0.5, ATR 2 -> level 97."""
    return mk(ts, open=100.0, high=101.0, low=95.0, close=96.0,
              bos_bear=1, break_depth=0.5, atr14=2.0, delta_pct=-0.3,
              vol_vs_ma=2.0)


def bull_break(ts):
    """H1 closes over swing high: close 104, depth 0.5, ATR 2 -> level 103."""
    return mk(ts, open=100.0, low=99.0, high=105.0, close=104.0,
              bos_bull=1, break_depth=0.5, atr14=2.0, delta_pct=0.3,
              vol_vs_ma=2.0)


class TestLongFlip(unittest.TestCase):
    """Failed bear break -> reclaim above level -> long win at 2R."""

    def test_reclaim_long_win(self):
        h1 = make_h1(50, 1, bear_break(T0 + H1_MS))
        m15 = flat_m15(T0 + 2 * H1_MS, 48)  # flat below the level (95 < 97)
        m15[0]['low'] = 94.0      # trap low (post-break extreme)
        m15[3]['close'] = 97.5    # reclaim: close back above level 97
        m15[4]['high'] = 105.0    # runs to target
        m15[4]['low'] = 97.0
        cfg = Config()
        setups = run_flips(h1, m15, cfg)
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s['direction'], 'long')
        self.assertEqual(s['outcome'], 'entered')
        self.assertAlmostEqual(s['break_level'], 97.0)   # 96 + 0.5*2
        self.assertAlmostEqual(s['entry_price'], 97.5)
        self.assertAlmostEqual(s['pullback_stop'], 93.9)  # 94 - 0.1*1
        t = simulate_trade(m15, s, 'pullback', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'target')
        self.assertGreater(t['r_net'], 0)
        self.assertAlmostEqual(t['target'], 97.5 + 2 * (97.5 - 93.9), places=6)


class TestDeltaSelection(unittest.TestCase):
    """--select delta: delta_turn flag from the last third of the window."""

    def _flip_setup(self, tail_delta):
        h1 = make_h1(50, 1, bear_break(T0 + H1_MS))
        m15 = flat_m15(T0 + 2 * H1_MS, 48)
        m15[0]['low'] = 94.0
        m15[1]['delta_pct'] = tail_delta
        m15[2]['delta_pct'] = tail_delta
        m15[3]['close'] = 97.5
        m15[3]['delta_pct'] = tail_delta
        return h1, m15

    def test_delta_turn_detected(self):
        h1, m15 = self._flip_setup(0.2)
        s = run_flips(h1, m15, Config())[0]
        self.assertEqual(s['outcome'], 'entered')
        self.assertEqual(s['delta_turn'], 1)

    def test_no_delta_turn(self):
        h1, m15 = self._flip_setup(-0.2)
        s = run_flips(h1, m15, Config())[0]
        self.assertEqual(s['outcome'], 'entered')
        self.assertEqual(s['delta_turn'], 0)


class TestNoReclaim(unittest.TestCase):
    def test_no_reclaim_within_48_h1_is_dead(self):
        h1 = make_h1(50, 1, bear_break(T0 + H1_MS))
        m15 = flat_m15(T0 + 2 * H1_MS, 48)  # never closes above 97
        cfg = Config()
        s = run_flips(h1, m15, cfg)[0]
        self.assertEqual(s['outcome'], 'missed')
        self.assertEqual(s['invalidation'], 'no_reclaim')


class TestFlipStop(unittest.TestCase):
    def test_stop_at_post_break_extreme(self):
        h1 = make_h1(50, 1, bear_break(T0 + H1_MS))
        m15 = flat_m15(T0 + 2 * H1_MS, 48)
        m15[0]['low'] = 94.0
        m15[3]['close'] = 97.5   # reclaim, entry 97.5, stop 93.9
        m15[4]['low'] = 93.85    # dips through the stop
        m15[4]['high'] = 98.0
        cfg = Config()
        s = run_flips(h1, m15, cfg)[0]
        t = simulate_trade(m15, s, 'pullback', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'stop')
        self.assertLess(t['r_net'], 0)
        # price component of the stop loss is exactly -1R (plus small costs)
        self.assertGreater(t['r_net'], -1.3)


class TestShortFlipMirror(unittest.TestCase):
    def test_failed_bull_break_short_win(self):
        h1 = make_h1(50, 1, bull_break(T0 + H1_MS), fill_close=105.0)
        m15 = flat_m15(T0 + 2 * H1_MS, 48, close=105.0, high=105.5, low=104.5)
        m15[0]['high'] = 106.0    # trap high
        m15[3]['close'] = 102.5   # reclaim: close back below level 103
        m15[4]['low'] = 94.0      # runs down through target
        m15[4]['high'] = 103.0
        cfg = Config()
        setups = run_flips(h1, m15, cfg)
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s['direction'], 'short')
        self.assertEqual(s['outcome'], 'entered')
        self.assertAlmostEqual(s['break_level'], 103.0)  # 104 - 0.5*2
        self.assertAlmostEqual(s['entry_price'], 102.5)
        self.assertAlmostEqual(s['pullback_stop'], 106.1)  # 106 + 0.1*1
        t = simulate_trade(m15, s, 'pullback', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'target')
        self.assertGreater(t['r_net'], 0)


if __name__ == '__main__':
    unittest.main()
