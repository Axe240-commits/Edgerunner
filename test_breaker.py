#!/usr/bin/env python3
"""Unit sanity tests for backtest_breaker (synthetic candles, no DB/network)."""
import unittest

from backtest_breaker import (Config, run_setups, run_setups_v2,
                              simulate_trade, H1_MS)

M15_MS = 900_000
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


def flat_m15(hour_start, hours, close=97.0, high=97.5, low=96.5, **kw):
    """Generate flat M15 bars for `hours` consecutive hours."""
    bars = []
    for h in range(hours):
        for q in range(4):
            bars.append(mk(hour_start + h * H1_MS + q * M15_MS,
                           close=close, high=high, low=low, **kw))
    return bars


def short_breaker(ts):
    """H1 break downwards: zone [100, 101], breaker high 101, ATR 2."""
    return mk(ts, open=100.0, high=101.0, low=95.0, close=96.0,
              bos_bear=1, break_depth=0.5, atr14=2.0, vol_vs_ma=2.0,
              delta_pct=-0.3)


def long_breaker(ts):
    """H1 break upwards: zone [99, 100], breaker low 99, ATR 2."""
    return mk(ts, open=100.0, low=99.0, high=105.0, close=104.0,
              bos_bull=1, break_depth=0.5, atr14=2.0, vol_vs_ma=2.0,
              delta_pct=0.3)


def make_h1(n, breaker_idx=1, breaker=None, fill_close=97.0):
    h1 = [mk(T0 + i * H1_MS, close=fill_close) for i in range(n)]
    if breaker is not None:
        h1[breaker_idx] = breaker
    return h1


class TestShortSetup(unittest.TestCase):
    """Full short cycle: break -> pullback -> entry -> 2R target hit."""

    def setUp(self):
        self.h1 = make_h1(50, 1, short_breaker(T0 + H1_MS))
        self.m15 = flat_m15(T0 + 2 * H1_MS, 48)
        # hour of h1[2]: dip to 94 (post-break low), then rise into the zone
        self.m15[0]['low'] = 94.0
        self.m15[2]['high'] = 100.5  # pullback starts (zone low = 100)
        self.m15[2]['vol_vs_ma'] = 0.8
        self.m15[2]['delta_pct'] = 0.05
        # hour of h1[3]: M15 closes back out of the zone -> entry at 99.5
        e = 4
        self.m15[e]['high'] = 100.2
        self.m15[e]['close'] = 99.5
        self.m15[e]['vol_vs_ma'] = 0.8
        self.m15[e]['delta_pct'] = 0.05
        # after entry: straight to target, low 95.4 <= TP 95.5, high < stop
        self.m15[5]['high'] = 100.0
        self.m15[5]['low'] = 95.4
        self.m15[5]['close'] = 96.0

    def test_setup_detected_and_won(self):
        cfg = Config()
        setups = run_setups(self.h1, self.m15, cfg)
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s['direction'], 'short')
        self.assertEqual(s['outcome'], 'entered')
        self.assertAlmostEqual(s['entry_price'], 99.5)
        self.assertAlmostEqual(s['breaker_stop'], 101.5)  # 101 + 0.25*2
        t = simulate_trade(self.m15, s, 'breaker', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'target')
        self.assertGreater(t['r_net'], 0)
        self.assertAlmostEqual(t['target'], 95.5, places=6)  # 99.5 - 2*2


class TestLongMirror(unittest.TestCase):
    def test_long_setup_detected_and_won(self):
        h1 = make_h1(50, 1, long_breaker(T0 + H1_MS), fill_close=103.0)
        m15 = flat_m15(T0 + 2 * H1_MS, 48, close=103.0, high=103.5, low=102.5)
        m15[0]['high'] = 106.0     # post-break extreme
        m15[2]['low'] = 99.5       # pullback into zone [99,100]
        m15[2]['vol_vs_ma'] = 0.8
        m15[2]['delta_pct'] = -0.05
        m15[4]['low'] = 99.8
        m15[4]['close'] = 100.5    # closes out of the zone -> entry
        m15[4]['vol_vs_ma'] = 0.8
        m15[4]['delta_pct'] = -0.05
        m15[5]['high'] = 104.6     # TP 104.5 hit
        m15[5]['low'] = 100.0
        cfg = Config()
        setups = run_setups(h1, m15, cfg)
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s['direction'], 'long')
        self.assertEqual(s['outcome'], 'entered')
        self.assertAlmostEqual(s['entry_price'], 100.5)
        self.assertAlmostEqual(s['breaker_stop'], 98.5)  # 99 - 0.25*2
        t = simulate_trade(m15, s, 'breaker', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'target')
        self.assertGreater(t['r_net'], 0)


class TestInvalidation(unittest.TestCase):
    def test_h1_close_beyond_breaker_high_kills_setup(self):
        h1 = make_h1(50, 1, short_breaker(T0 + H1_MS))
        h1[2]['close'] = 102.0  # H1 close above breaker high 101 -> dead
        m15 = flat_m15(T0 + 2 * H1_MS, 48)
        m15[8]['high'] = 100.5  # zone touched later — must not matter
        cfg = Config()
        s = run_setups(h1, m15, cfg)[0]
        self.assertEqual(s['outcome'], 'invalid')
        self.assertEqual(s['invalidation'],
                         'h1_close_beyond_breaker_before_pullback')
        self.assertFalse(s['entered'])

    def test_no_pullback_within_48_h1_is_missed(self):
        h1 = make_h1(50, 1, short_breaker(T0 + H1_MS))
        m15 = flat_m15(T0 + 2 * H1_MS, 48)  # highs stay at 97.5 < zone 100
        cfg = Config()
        s = run_setups(h1, m15, cfg)[0]
        self.assertEqual(s['outcome'], 'missed')
        self.assertEqual(s['invalidation'], 'no_pullback')


class TestH4Timeframes(unittest.TestCase):
    """TF-generic path: setup on 4h candles, execution on 1h bars (v1 rules)."""

    def test_h4_setup_1h_exec(self):
        h4_ms = 4 * H1_MS
        h4 = [mk(T0 + i * h4_ms, close=97.0) for i in range(30)]
        h4[1] = short_breaker(T0 + h4_ms)  # zone [100,101], ATR(4h) 2
        # 1h exec bars from the candle after the breaker
        m60 = [mk(T0 + 2 * h4_ms + k * H1_MS, close=97.0, high=97.5, low=96.5)
               for k in range(4 * 28)]
        m60[0]['low'] = 94.0        # post-break extreme
        m60[2]['high'] = 100.5      # zone touch
        m60[2]['vol_vs_ma'] = 0.8
        m60[2]['delta_pct'] = 0.05
        m60[4]['high'] = 100.2      # trades in zone and closes out -> entry
        m60[4]['close'] = 99.5
        m60[4]['vol_vs_ma'] = 0.8
        m60[4]['delta_pct'] = 0.05
        m60[5]['high'] = 100.0
        m60[5]['low'] = 95.4        # hits 2R target (95.5)
        cfg = Config(strategy='v1', setup_tf='4h', exec_tf='1h',
                     validity_h1=24)
        setups = run_setups(h4, m60, cfg)
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s['outcome'], 'entered')
        self.assertAlmostEqual(s['entry_price'], 99.5)
        self.assertAlmostEqual(s['breaker_stop'], 101.5)  # 101 + 0.25*ATR(4h)
        t = simulate_trade(m60, s, 'breaker', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'target')
        self.assertGreater(t['r_net'], 0)


class TestV2M1Trigger(unittest.TestCase):
    """v2 full cycle: H1 break -> M15 zone touch -> M1 BOS -> entry -> win."""

    def _build(self):
        h1 = make_h1(50, 1, short_breaker(T0 + H1_MS))
        m15 = flat_m15(T0 + 2 * H1_MS, 48)
        m15[0]['low'] = 94.0        # post-break extreme (v2 target)
        m15[2]['high'] = 100.5      # zone touch at T0+2H+30min
        touch_ts = m15[2]['timestamp']
        # M1 bars from the touch candle onward (flat inside the zone)
        m1 = [mk(touch_ts + k * 60_000, close=100.2, high=100.3, low=99.8)
              for k in range(120)]
        # M1 structure break downwards at bar 10
        m1[10].update(bos_bear=1, break_depth=0.2, atr14=0.5,
                      high=100.4, close=99.7)
        # afterwards: straight down through the post-break extreme
        for k in range(11, 20):
            m1[k].update(high=99.6, low=93.9 if k == 11 else 93.5, close=94.0)
        return h1, m15, m1

    def _loader(self, m1):
        return lambda start, end: [c for c in m1
                                   if start <= c['timestamp'] < end]

    def test_m1_trigger_entry_and_target_win(self):
        h1, m15, m1 = self._build()
        cfg = Config(strategy='v2')
        setups = run_setups_v2(h1, m15, cfg, self._loader(m1))
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s['outcome'], 'entered')
        self.assertAlmostEqual(s['entry_price'], 99.7)
        # stop = max M1 high since touch (100.4) + 0.1 x ATR(M1)=0.05
        self.assertAlmostEqual(s['pullback_stop'], 100.45)
        self.assertAlmostEqual(s['target_price'], 94.0)  # post-break extreme
        t = simulate_trade(s['_sim_candles'], s, 'pullback',
                           ('price', s['target_price']), cfg)
        self.assertEqual(t['exit_reason'], 'target')
        self.assertGreater(t['r_net'], 0)

    def test_m15_close_beyond_breaker_invalidates(self):
        h1, m15, m1 = self._build()
        # Move the M1 trigger behind the invalidation: M15 close above
        # breaker high (101) at m15[4] -> window ends at m15[4]+15min,
        # the BOS at touch+50min lies outside -> invalid, no entry.
        m1[10].update(bos_bear=0, break_depth=0.0)
        m1[50].update(bos_bear=1, break_depth=0.2, atr14=0.5, close=99.7)
        m15[4]['close'] = 102.0
        cfg = Config(strategy='v2')
        s = run_setups_v2(h1, m15, cfg, self._loader(m1))[0]
        self.assertEqual(s['outcome'], 'invalid')
        self.assertEqual(s['invalidation'],
                         'm15_close_beyond_breaker_during_m1_wait')
        self.assertFalse(s['entered'])

    def test_entry_too_far_is_missed(self):
        h1, m15, m1 = self._build()
        # BOS candle closes 1.5 zone heights below the zone edge (limit: 1.0)
        m1[10]['close'] = 98.5
        cfg = Config(strategy='v2')
        s = run_setups_v2(h1, m15, cfg, self._loader(m1))[0]
        self.assertEqual(s['outcome'], 'missed')
        self.assertEqual(s['invalidation'], 'entry_too_far')
        self.assertAlmostEqual(s['entry_distance_zone_h'], 1.5)


class TestCostAccounting(unittest.TestCase):
    """Stop exits must equal exactly -(1R + cost share), never more.

    Guards the loss-normalization: costs are charged on notional, so in R
    terms they scale with entry/risk (tight stop -> big cost share). The
    price component of a stop loss must stay exactly -1R.
    """

    def test_stop_loss_is_one_r_plus_costs(self):
        h1 = make_h1(50, 1, short_breaker(T0 + H1_MS))
        m15 = flat_m15(T0 + 2 * H1_MS, 48)
        m15[2]['high'] = 100.5
        m15[2]['vol_vs_ma'] = 0.8
        m15[2]['delta_pct'] = 0.05
        m15[4]['high'] = 100.2
        m15[4]['close'] = 99.5  # entry
        m15[5]['high'] = 101.6  # above stop 101.5 -> stopped
        m15[5]['low'] = 99.0
        cfg = Config()
        s = run_setups(h1, m15, cfg)[0]
        t = simulate_trade(m15, s, 'breaker', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'stop')
        cf = cfg.cost_frac
        risk = abs(t['entry'] - t['stop'])
        expected = -1.0 - cf * (t['entry'] + t['stop']) / risk
        self.assertAlmostEqual(t['r_net'], expected, places=9)
        # with default 10 bps/side and this stop distance: ~-1.1R, not ~-1.7R
        self.assertGreater(t['r_net'], -1.2)


class TestSameBarConflict(unittest.TestCase):
    def test_same_bar_stop_and_target_counts_as_stop(self):
        h1 = make_h1(50, 1, short_breaker(T0 + H1_MS))
        m15 = flat_m15(T0 + 2 * H1_MS, 48)
        m15[2]['high'] = 100.5
        m15[2]['vol_vs_ma'] = 0.8
        m15[2]['delta_pct'] = 0.05
        m15[4]['high'] = 100.2
        m15[4]['close'] = 99.5  # entry
        # next bar tags BOTH stop (>=101.5) and target (<=95.5): -> stop
        m15[5]['high'] = 102.0
        m15[5]['low'] = 95.0
        cfg = Config()
        s = run_setups(h1, m15, cfg)[0]
        self.assertEqual(s['outcome'], 'entered')
        t = simulate_trade(m15, s, 'breaker', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'stop')
        self.assertLess(t['r_net'], 0)


class TestTimeoutExit(unittest.TestCase):
    def test_trade_times_out_mark_to_market(self):
        h1 = make_h1(50, 1, short_breaker(T0 + H1_MS))
        m15 = flat_m15(T0 + 2 * H1_MS, 48)
        m15[2]['high'] = 100.5
        m15[2]['vol_vs_ma'] = 0.8
        m15[2]['delta_pct'] = 0.05
        m15[4]['high'] = 100.2
        m15[4]['close'] = 99.5  # entry; afterwards flat at 97 forever
        cfg = Config(max_hold_bars=10)
        s = run_setups(h1, m15, cfg)[0]
        t = simulate_trade(m15, s, 'breaker', ('r', 2.0), cfg)
        self.assertEqual(t['exit_reason'], 'timeout')
        self.assertEqual(t['bars_held'], 10)
        # flat at 97, entry 99.5 short -> small win minus costs
        self.assertAlmostEqual(t['exit'], 97.0)


if __name__ == '__main__':
    unittest.main()
