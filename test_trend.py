#!/usr/bin/env python3
"""Unit tests for backtest_trend (synthetic candles, no DB/network)."""
import unittest

from backtest_breaker import Config, simulate_trade, H1_MS
from backtest_trend import run_continuations, simulate_trail

H4_MS = 4 * H1_MS
T0 = 1_700_000_000_000


def mk(ts, **kw):
    c = {
        'timestamp': ts, 'open': 100.0, 'high': 100.5, 'low': 99.5,
        'close': 100.0, 'volume': 100.0, 'delta': 0.0, 'delta_pct': 0.0,
        'vol_vs_ma': 1.0, 'atr14': 1.0, 'bos_bull': 0, 'bos_bear': 0,
        'break_depth': 0.0,
    }
    c.update(kw)
    return c


def bull_break(ts):
    """H4 BOS up: close 104, ATR 2 -> entry 104, stop 101 (1.5xATR)."""
    return mk(ts, open=100.0, low=99.0, high=105.0, close=104.0,
              bos_bull=1, break_depth=0.5, atr14=2.0)


def make_setup(n=30):
    return [mk(T0 + i * H4_MS, close=100.0) for i in range(n)]


def flat_exec(start_ts, n_bars, **kw):
    base = dict(close=105.0, high=105.5, low=104.5)
    base.update(kw)
    return [mk(start_ts + k * H1_MS, **base) for k in range(n_bars)]


class TestContinuationEntry(unittest.TestCase):
    def test_bos_long_entry_and_2r_win(self):
        h4 = make_setup()
        h4[1] = bull_break(T0 + H4_MS)
        ex = flat_exec(h4[2]['timestamp'], 100)
        ex[1]['high'] = 110.1   # 2R target = 104 + 2*3 = 110
        cfg = Config(setup_tf='4h', exec_tf='1h')
        cfg.trend_stop_atr_mult = 1.5
        setups = run_continuations(h4, ex, cfg)
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s['direction'], 'long')
        self.assertAlmostEqual(s['entry_price'], 104.0)
        self.assertAlmostEqual(s['pullback_stop'], 101.0)  # 104 - 1.5*2
        t = simulate_trade(ex, s, 'pullback', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'target')
        self.assertGreater(t['r_net'], 0)

    def test_chandelier_trail_exit(self):
        h4 = make_setup()
        h4[1] = bull_break(T0 + H4_MS)
        ex = flat_exec(h4[2]['timestamp'], 100)
        ex[1]['high'] = 112.0   # extreme -> trail = 112 - 3*2 = 106
        ex[2]['high'] = 111.0
        ex[3]['low'] = 105.9    # below trail -> exit at 106
        cfg = Config(setup_tf='4h', exec_tf='1h')
        cfg.trend_stop_atr_mult = 1.5
        s = run_continuations(h4, ex, cfg)[0]
        t = simulate_trail(ex, s, cfg)
        self.assertEqual(t['exit_reason'], 'trail')
        self.assertAlmostEqual(t['exit'], 106.0)
        self.assertGreater(t['r_net'], 0)  # (106-104)/3 minus small costs

    def test_bos_bear_goes_short(self):
        h4 = make_setup()
        h4[1] = mk(T0 + H4_MS, open=100.0, high=101.0, low=95.0, close=96.0,
                   bos_bear=1, break_depth=0.5, atr14=2.0)
        ex = flat_exec(h4[2]['timestamp'], 10)
        cfg = Config(setup_tf='4h', exec_tf='1h')
        cfg.trend_stop_atr_mult = 1.5
        s = run_continuations(h4, ex, cfg)[0]
        self.assertEqual(s['direction'], 'short')
        self.assertAlmostEqual(s['pullback_stop'], 99.0)  # 96 + 1.5*2


if __name__ == '__main__':
    unittest.main()
