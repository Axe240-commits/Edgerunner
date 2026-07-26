#!/usr/bin/env python3
"""
Breaker Zone Backtest (STRATEGY_V1) — honest, point-in-time correct.

Implements the contract in STRATEGY_V1.md:

  H1 structure break (close beyond swing level, bos_bear/bos_bull with
  break_depth > 0) -> breaker candle -> zone (short: open..high, long:
  low..open) -> M15 pullback into the zone with weaker volume/delta than
  the impulse -> entry when an M15 candle closes back out of the zone in
  break direction. Stop = breaker extreme + 0.25 x ATR(14, H1), baseline
  target 2R. Validity 48 H1 candles; dead on H1 close beyond the breaker
  extreme.

Point-in-time rules (same as the research-runner fix):
  - H1 features of a candle are used only after that candle has CLOSED.
  - M15 entry simulation starts strictly after the breaker candle's close.
  - H1-close invalidation takes effect from the NEXT hour onward.

Costs: fee + slippage per side, applied to entry and exit prices.
Same-bar stop/TP conflict resolves conservatively to the STOP.

Modes:
  diagnose — train prefix only (first --train-fraction of H1 candles):
             pullback depth distributions, target comparison
             (1.5R/2R/3R/low-after-break), stop variants, missed trades.
  validate — ONE config, ONE out-of-sample run (remaining 35%):
             Wilson-95% CI, net expectancy, kill criteria -> PASS/FAIL.

The DB is opened READ-ONLY. Reports go to --json-out / --md-out files only.

Usage:
    python3 backtest_breaker.py --db edgerunner.db --mode diagnose \
        --since 2024-01-01 --until 2026-01-01 --json-out diag.json
"""
import argparse
import json
import sqlite3
import sys
from bisect import bisect_left
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone

H1_MS = 3_600_000
M15_MS = 900_000

# Timeframe durations for the generic --setup-tf/--exec-tf plumbing.
TF_MS = {
    '1m': 60_000, '3m': 180_000, '5m': 300_000, '10m': 600_000,
    '15m': 900_000, '30m': 1_800_000, '1h': 3_600_000, '2h': 7_200_000,
    '4h': 14_400_000, '1d': 86_400_000,
}

# H1/M15 columns this backtest relies on (verified against db.py
# FEATURE_COLUMNS and PRAGMA table_info on the live DB).
CANDLE_COLS = ('timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta',
               'delta_pct', 'vol_vs_ma', 'atr14', 'bos_bull', 'bos_bear',
               'break_depth')


@dataclass
class Config:
    fee_bps: float = 2.0           # v2 default (spec: limit-oriented 2+5)
    slippage_bps: float = 5.0
    train_fraction: float = 0.65
    validity_h1: int = 48          # validity in SETUP-tf candles (48 H1 = 2d; 24 H4 = 4d)
    max_hold_bars: int = 96        # v1: exec-tf bars max in trade
    stop_atr_mult: float = 0.25    # v1: breaker extreme + 0.25 x ATR(14, setup tf)
    pullback_vol_ratio: float = 1.0   # v1 pullback filter
    pullback_delta_ratio: float = 1.0
    require_pullback_filter: bool = True
    baseline_target_r: float = 2.0
    strategy: str = 'v2'
    setup_tf: str = '1h'           # timeframe of break detection (bos candles)
    exec_tf: str = '15m'           # timeframe of pullback tracking / trade sim
    # v2 (M1 trigger)
    m1_stop_atr_mult: float = 0.1    # stop behind M1 pullback extreme + 0.1 x ATR(14, M1)
    entry_max_zone_dist: float = 1.0  # entry at most 1 x zone height beyond zone edge
    m1_timeout_bars: int = 5760      # 96 M15 equivalents in M1 bars (4 days)

    @property
    def cost_frac(self):
        return (self.fee_bps + self.slippage_bps) / 10_000.0


# ---------------------------------------------------------------------------
# Data access (read-only)
# ---------------------------------------------------------------------------

def load_candles(conn, tf, since_ms, until_ms):
    """Load candles for one timeframe, ordered by timestamp."""
    table = f'candles_{tf}'
    cols = ', '.join(CANDLE_COLS)
    sql = f'SELECT {cols} FROM {table} WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp'
    rows = conn.execute(sql, (since_ms, until_ms)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Setup tracking (pure functions on candle dicts — unit-testable without DB)
# ---------------------------------------------------------------------------

def _pullback_filter_ok(direction, pull_candles, breaker, cfg):
    """Pullback must be WEAKER than the impulse (spec rule 3).

    Short: average pullback vol_vs_ma below the breaker H1 vol_vs_ma and the
    average positive delta_pct below the breaker's |delta_pct| (each scaled by
    the configured ratio). Conditions are only enforced when the impulse
    reference is > 0; otherwise they pass (documented weakness: cross-TF
    vol_vs_ma comparison is rough).
    """
    if not cfg.require_pullback_filter or not pull_candles:
        return True
    n = len(pull_candles)
    avg_vol = sum((c.get('vol_vs_ma') or 0) for c in pull_candles) / n
    impulse_vol = breaker.get('vol_vs_ma') or 0
    if impulse_vol > 0 and avg_vol >= impulse_vol * cfg.pullback_vol_ratio:
        return False
    if direction == 'short':
        avg_d = sum(max(c.get('delta_pct') or 0, 0.0) for c in pull_candles) / n
    else:
        avg_d = sum(max(-(c.get('delta_pct') or 0), 0.0) for c in pull_candles) / n
    impulse_d = abs(breaker.get('delta_pct') or 0)
    if impulse_d > 0 and avg_d >= impulse_d * cfg.pullback_delta_ratio:
        return False
    return True


def _track_setup(h1, i, direction, m15, m15_ts, cfg):
    """Track one setup-tf break on exec-tf bars until entry/invalid/timeout.

    `h1`/`m15` are the setup-tf resp. exec-tf candle lists (names kept for
    historical reasons). Returns a setup record dict; trades are evaluated
    per config afterwards.
    """
    setup_ms = TF_MS[cfg.setup_tf]
    b = h1[i]
    short = direction == 'short'
    atr = b.get('atr14') or 0.0
    zone_low, zone_high = ((b['open'], b['high']) if short
                           else (b['low'], b['open']))
    breaker_extreme = b['high'] if short else b['low']
    # Reconstruct the broken swing level from the ATR-normalized break_depth
    # (analyzer: break_depth = |close - level| / atr).
    depth = b.get('break_depth') or 0.0
    level = b['close'] + depth * atr if short else b['close'] - depth * atr

    rec = {
        'direction': direction,
        'breaker_ts': b['timestamp'],
        'breaker_index': i,
        'zone_low': zone_low,
        'zone_high': zone_high,
        'breaker_extreme': breaker_extreme,
        'swing_level': level,
        'atr': atr,
        'outcome': None,
        'invalidation': None,
        'entered': False,
    }
    if atr <= 0 or zone_high <= zone_low:
        rec['outcome'] = 'skipped'
        rec['invalidation'] = 'missing_atr_or_empty_zone'
        return rec

    end_j = min(i + cfg.validity_h1, len(h1) - 1)
    pull_started = False
    pull_candles = []
    pull_extreme = None          # max high (short) / min low (long) of pullback
    post_extreme = None          # min low (short) / max high (long) after break
    filter_ever_ok = False

    for j in range(i + 1, end_j + 1):
        hour_start = h1[j]['timestamp']
        lo = bisect_left(m15_ts, hour_start)
        hi = bisect_left(m15_ts, hour_start + setup_ms)
        for k in range(lo, hi):
            c = m15[k]
            if short:
                if not pull_started:
                    post_extreme = (c['low'] if post_extreme is None
                                    else min(post_extreme, c['low']))
                    if c['high'] >= zone_low:
                        pull_started = True
                        pull_candles.append(c)
                        pull_extreme = c['high']
                else:
                    pull_candles.append(c)
                    pull_extreme = max(pull_extreme, c['high'])
                    # Entry: the candle must actually trade IN the zone
                    # (high >= zone_low) and close OUT of it — a candle that
                    # never re-enters the zone is not an exit from it.
                    if (c['high'] >= zone_low and c['close'] < zone_low
                            and _pullback_filter_ok(
                                direction, pull_candles, b, cfg)):
                        rec['entered'] = True
                        rec['entry_idx'] = k
                        break
            else:
                if not pull_started:
                    post_extreme = (c['high'] if post_extreme is None
                                    else max(post_extreme, c['high']))
                    if c['low'] <= zone_high:
                        pull_started = True
                        pull_candles.append(c)
                        pull_extreme = c['low']
                else:
                    pull_candles.append(c)
                    pull_extreme = min(pull_extreme, c['low'])
                    if (c['low'] <= zone_high and c['close'] > zone_high
                            and _pullback_filter_ok(
                                direction, pull_candles, b, cfg)):
                        rec['entered'] = True
                        rec['entry_idx'] = k
                        break
            if _pullback_filter_ok(direction, pull_candles, b, cfg):
                filter_ever_ok = True
        if rec['entered']:
            break
        # Setup-tf close invalidation — the close of candle j is known only
        # after it, so it cancels the setup from candle j+1 onward.
        if short and h1[j]['close'] > breaker_extreme:
            rec['outcome'] = 'invalid'
            rec['invalidation'] = 'h1_close_beyond_breaker'
            break
        if not short and h1[j]['close'] < breaker_extreme:
            rec['outcome'] = 'invalid'
            rec['invalidation'] = 'h1_close_beyond_breaker'
            break

    if rec['entered']:
        c = m15[rec['entry_idx']]
        rec['outcome'] = 'entered'
        rec['entry_ts'] = c['timestamp']
        rec['entry_price'] = c['close']
        rec['max_hold'] = cfg.max_hold_bars
        buf = cfg.stop_atr_mult * atr
        rec['breaker_stop'] = breaker_extreme + buf if short else breaker_extreme - buf
        rec['pullback_stop'] = pull_extreme + buf if short else pull_extreme - buf
        rec['post_extreme'] = post_extreme
        rec['pullback_extreme'] = pull_extreme
        rec['pullback_bars'] = len(pull_candles)
        # Pullback depth diagnostics: % of impulse leg and % of zone height.
        impulse = abs(level - post_extreme) if post_extreme is not None else 0
        into_zone = (abs(pull_extreme - zone_low) if short
                     else abs(zone_high - pull_extreme))
        rec['depth_pct_impulse'] = (abs(pull_extreme - post_extreme) / impulse
                                    if impulse > 0 else None)
        zone_h = zone_high - zone_low
        rec['depth_pct_zone'] = into_zone / zone_h if zone_h > 0 else None
        return rec

    if rec['outcome'] is None:
        if end_j < i + cfg.validity_h1:
            rec['outcome'] = 'incomplete'   # data ends before validity window
        elif not pull_started:
            rec['outcome'] = 'missed'
            rec['invalidation'] = 'no_pullback'
        else:
            rec['outcome'] = 'missed'
            rec['invalidation'] = ('filter_never_passed' if not filter_ever_ok
                                   else 'no_exit_close')
    else:  # invalidated before entry — distinguish whether zone was reached
        if not pull_started:
            rec['invalidation'] += '_before_pullback'
    return rec


def run_setups(h1, m15, cfg):
    """Detect all H1 breaks and track each on M15."""
    m15_ts = [c['timestamp'] for c in m15]
    setups = []
    for i, b in enumerate(h1):
        depth = b.get('break_depth') or 0
        if depth <= 0:
            continue
        if b.get('bos_bear') == 1:
            setups.append(_track_setup(h1, i, 'short', m15, m15_ts, cfg))
        if b.get('bos_bull') == 1:
            setups.append(_track_setup(h1, i, 'long', m15, m15_ts, cfg))
    return setups


# ---------------------------------------------------------------------------
# v2: M1 trigger (spec section "v2")
# ---------------------------------------------------------------------------
# Two passes per setup:
#   Pass 1 (H1/M15): find the zone touch and the validity window. v2
#     invalidation is an M15 CLOSE beyond the breaker extreme (replaces the
#     old H1-close rule) or the 48-H1 timeout.
#   Pass 2 (M1): scan the window for an M1 structure break in break
#     direction (bos_bear/bos_bull on candles_1m, computed by the same
#     analyzer). Entry = close of the M1 BOS candle, but only if it closes
#     within entry_max_zone_dist x zone height of the zone edge.
#   Stop: M1 pullback extreme since zone touch + 0.1 x ATR(14, M1).
#   Target: post-break extreme (extreme between breaker close and zone touch).

def _track_setup_v2(h1, i, direction, m15, m15_ts, cfg):
    """v2 pass 1: zone touch + validity window (H1/M15 only, no M1 yet)."""
    b = h1[i]
    short = direction == 'short'
    atr = b.get('atr14') or 0.0
    zone_low, zone_high = ((b['open'], b['high']) if short
                           else (b['low'], b['open']))
    breaker_extreme = b['high'] if short else b['low']
    depth = b.get('break_depth') or 0.0
    level = b['close'] + depth * atr if short else b['close'] - depth * atr

    rec = {
        'direction': direction,
        'strategy': 'v2',
        'breaker_ts': b['timestamp'],
        'breaker_index': i,
        'zone_low': zone_low,
        'zone_high': zone_high,
        'breaker_extreme': breaker_extreme,
        'swing_level': level,
        'atr': atr,
        'outcome': None,
        'invalidation': None,
        'entered': False,
    }
    if atr <= 0 or zone_high <= zone_low:
        rec['outcome'] = 'skipped'
        rec['invalidation'] = 'missing_atr_or_empty_zone'
        return rec

    setup_ms = TF_MS[cfg.setup_tf]
    exec_ms = TF_MS[cfg.exec_tf]
    end_j = min(i + cfg.validity_h1, len(h1) - 1)
    rec['validity_end_ts'] = h1[end_j]['timestamp'] + setup_ms
    incomplete = end_j < i + cfg.validity_h1

    touch_ts = None
    post_extreme = None
    invalid_ts = None
    for j in range(i + 1, end_j + 1):
        hour_start = h1[j]['timestamp']
        lo = bisect_left(m15_ts, hour_start)
        hi = bisect_left(m15_ts, hour_start + setup_ms)
        for k in range(lo, hi):
            c = m15[k]
            if touch_ts is None:
                # Zone touch: the touching exec candle's high/low is only
                # known INTRABAR — strictly closed rule: the touch takes
                # effect from the candle's CLOSE (timestamp + exec_ms), so
                # the M1 scan never uses the still-running exec candle.
                if short:
                    post_extreme = (c['low'] if post_extreme is None
                                    else min(post_extreme, c['low']))
                    if c['high'] >= zone_low:
                        touch_ts = c['timestamp'] + exec_ms
                else:
                    post_extreme = (c['high'] if post_extreme is None
                                    else max(post_extreme, c['high']))
                    if c['low'] <= zone_high:
                        touch_ts = c['timestamp'] + exec_ms
            # v2 invalidation: exec-tf CLOSE beyond the breaker extreme. The
            # close is known only at the end of the candle, so it takes
            # effect from the next exec bar onward (point-in-time).
            if invalid_ts is None:
                if short and c['close'] > breaker_extreme:
                    invalid_ts = c['timestamp'] + exec_ms
                elif not short and c['close'] < breaker_extreme:
                    invalid_ts = c['timestamp'] + exec_ms

    rec['post_extreme'] = post_extreme
    rec['invalid_ts'] = invalid_ts

    if touch_ts is None:
        if invalid_ts is not None:
            rec['outcome'] = 'invalid'
            rec['invalidation'] = 'm15_close_beyond_breaker_before_pullback'
        elif incomplete:
            rec['outcome'] = 'incomplete'
        else:
            rec['outcome'] = 'missed'
            rec['invalidation'] = 'no_pullback'
        return rec

    rec['outcome'] = 'touched'  # intermediate; resolved by _enter_v2
    rec['zone_touch_ts'] = touch_ts
    rec['window_end'] = min(invalid_ts or rec['validity_end_ts'],
                            rec['validity_end_ts'])
    return rec


def _enter_v2(rec, m1, cfg):
    """v2 pass 2: scan the M1 window for a structure break in break direction."""
    short = rec['direction'] == 'short'
    zone_low, zone_high = rec['zone_low'], rec['zone_high']
    zone_h = zone_high - zone_low
    max_dist = cfg.entry_max_zone_dist * zone_h

    extreme = None  # M1 pullback extreme since zone touch (stop anchor)
    for k, c in enumerate(m1):
        if short:
            extreme = c['high'] if extreme is None else max(extreme, c['high'])
            triggered = (c.get('bos_bear') == 1
                         and (c.get('break_depth') or 0) > 0)
        else:
            extreme = c['low'] if extreme is None else min(extreme, c['low'])
            triggered = (c.get('bos_bull') == 1
                         and (c.get('break_depth') or 0) > 0)
        if not triggered:
            continue

        atr_m1 = c.get('atr14') or 0.0
        if atr_m1 <= 0:
            continue
        entry = c['close']
        # Distance rule: entry must not be further than entry_max_zone_dist
        # zone heights beyond the zone edge (no chasing a runaway move).
        if short:
            dist = zone_low - entry
        else:
            dist = entry - zone_high
        if dist > max_dist:
            rec['outcome'] = 'missed'
            rec['invalidation'] = 'entry_too_far'
            rec['entry_distance_zone_h'] = round(dist / zone_h, 3)
            return rec

        rec['entered'] = True
        rec['outcome'] = 'entered'
        rec['entry_idx'] = k
        rec['entry_ts'] = c['timestamp']
        rec['entry_price'] = entry
        rec['max_hold'] = cfg.m1_timeout_bars
        buf = cfg.m1_stop_atr_mult * atr_m1
        rec['pullback_stop'] = (extreme + buf) if short else (extreme - buf)
        h1_buf = cfg.stop_atr_mult * rec['atr']
        rec['breaker_stop'] = (rec['breaker_extreme'] + h1_buf if short
                               else rec['breaker_extreme'] - h1_buf)
        rec['target_price'] = rec['post_extreme']
        rec['pullback_extreme'] = extreme
        # Pullback depth diagnostics (same definitions as v1)
        impulse = (abs(rec['swing_level'] - rec['post_extreme'])
                   if rec['post_extreme'] is not None else 0)
        into_zone = (abs(extreme - zone_low) if short
                     else abs(zone_high - extreme))
        rec['depth_pct_impulse'] = (abs(extreme - rec['post_extreme']) / impulse
                                    if impulse > 0 and rec['post_extreme'] is not None
                                    else None)
        rec['depth_pct_zone'] = into_zone / zone_h if zone_h > 0 else None
        return rec

    # No M1 trigger inside the window — classify why.
    if rec.get('invalid_ts') is not None:
        rec['outcome'] = 'invalid'
        rec['invalidation'] = 'm15_close_beyond_breaker_during_m1_wait'
    elif rec['window_end'] < rec['validity_end_ts']:
        rec['outcome'] = 'incomplete'
    else:
        rec['outcome'] = 'missed'
        rec['invalidation'] = 'no_m1_trigger'
    return rec


def run_setups_v2(h1, m15, cfg, m1_loader):
    """v2: pass 1 on H1/M15, then per-setup M1 windows via m1_loader."""
    m15_ts = [c['timestamp'] for c in m15]
    setups = []
    for i, b in enumerate(h1):
        depth = b.get('break_depth') or 0
        if depth <= 0:
            continue
        for direction, flag in (('short', 'bos_bear'), ('long', 'bos_bull')):
            if b.get(flag) != 1:
                continue
            rec = _track_setup_v2(h1, i, direction, m15, m15_ts, cfg)
            if rec['outcome'] == 'touched':
                m1 = m1_loader(rec['zone_touch_ts'], rec['window_end'])
                rec['_sim_candles'] = m1
                _enter_v2(rec, m1, cfg)
            setups.append(rec)
    return setups


def _sim_candles(setup, m15):
    """Simulation bars for a setup: v2 setups carry their own M1 window."""
    return setup.get('_sim_candles') or m15


def compute_setups(h1, m15, cfg, m1_loader=None):
    if cfg.strategy == 'v2':
        if m1_loader is None:
            raise ValueError('v2 needs m1_loader')
        return run_setups_v2(h1, m15, cfg, m1_loader)
    return run_setups(h1, m15, cfg)


# ---------------------------------------------------------------------------
# Trade simulation (M15 bars after entry)
# ---------------------------------------------------------------------------

def simulate_trade(candles, setup, stop_mode, target, cfg):
    """Simulate one entered setup with a given stop variant and target.

    candles: the simulation timeframe bars (v1: M15, v2: the setup's M1
    window). setup['entry_idx'] indexes INTO this list.
    target: ('r', multiple) or ('price', absolute_price).
    Same-bar stop/TP conflict resolves to STOP (conservative).
    Returns a trade dict with r_net (after costs) and exit reason.
    """
    short = setup['direction'] == 'short'
    entry = setup['entry_price']
    stop = setup['breaker_stop'] if stop_mode == 'breaker' else setup['pullback_stop']
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    if target[0] == 'r':
        tp = entry - target[1] * risk if short else entry + target[1] * risk
    else:
        tp = target[1]
        # Target must lie in trade direction, else the trade is not evaluable.
        if (short and tp >= entry) or (not short and tp <= entry):
            return None

    max_hold = setup.get('max_hold', cfg.max_hold_bars)
    cf = cfg.cost_frac
    entry_eff = entry * (1 - cf) if short else entry * (1 + cf)
    start = setup['entry_idx'] + 1
    end = min(start + max_hold, len(candles))
    bars_held = 0
    exit_price = None
    reason = 'timeout'

    for k in range(start, end):
        c = candles[k]
        bars_held += 1
        if short:
            hit_stop = c['high'] >= stop
            hit_tp = c['low'] <= tp
        else:
            hit_stop = c['low'] <= stop
            hit_tp = c['high'] >= tp
        if hit_stop:  # conservative: conflict counts as stop
            exit_price = stop
            reason = 'stop'
            break
        if hit_tp:
            exit_price = tp
            reason = 'target'
            break
    if exit_price is None and end > start:
        exit_price = candles[end - 1]['close']  # mark-to-market
    if exit_price is None:
        return None

    if reason == 'target':
        exit_eff = exit_price * (1 + cf) if short else exit_price * (1 - cf)
    elif reason == 'stop':
        exit_eff = exit_price * (1 + cf) if short else exit_price * (1 - cf)
    else:
        exit_eff = exit_price * (1 + cf) if short else exit_price * (1 - cf)

    r_net = ((entry_eff - exit_eff) if short else (exit_eff - entry_eff)) / risk
    return {
        'direction': setup['direction'],
        'entry_ts': setup['entry_ts'],
        'entry': entry,
        'stop': stop,
        'target': tp,
        'exit': exit_price,
        'exit_reason': reason,
        'bars_held': bars_held,
        'r_net': r_net,
        'win': r_net > 0,
    }


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def wilson_ci(wins, n, z=1.96):
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (center - half, center + half)


def trade_stats(trades):
    n = len(trades)
    if n == 0:
        return {'n': 0}
    wins = sum(1 for t in trades if t['win'])
    wr = wins / n
    total_r = sum(t['r_net'] for t in trades)
    exp_r = total_r / n
    lo, hi = wilson_ci(wins, n)
    win_rs = [t['r_net'] for t in trades if t['win']]
    loss_rs = [-t['r_net'] for t in trades if not t['win']]
    avg_win = sum(win_rs) / len(win_rs) if win_rs else 0.0
    avg_loss = sum(loss_rs) / len(loss_rs) if loss_rs else 0.0
    breakeven_wr = avg_loss / (avg_win + avg_loss) if (avg_win + avg_loss) > 0 else None
    return {
        'n': n,
        'wins': wins,
        'win_rate': round(wr, 4),
        'wilson95': [round(lo, 4), round(hi, 4)],
        'net_r': round(total_r, 2),
        'expectancy_r': round(exp_r, 4),
        'avg_win_r': round(avg_win, 3),
        'avg_loss_r': round(avg_loss, 3),
        'breakeven_wr': round(breakeven_wr, 4) if breakeven_wr else None,
    }


def _percentiles(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    def q(p):
        k = (len(vals) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(vals) - 1)
        frac = k - lo
        return vals[lo] + (vals[hi] - vals[lo]) * frac
    return {
        'n': len(vals),
        'mean': round(sum(vals) / len(vals), 4),
        'p25': round(q(0.25), 4),
        'p50': round(q(0.50), 4),
        'p75': round(q(0.75), 4),
        'p90': round(q(0.90), 4),
    }


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def _count_outcomes(setups):
    counts = {}
    for s in setups:
        key = s['outcome'] if s['outcome'] != 'missed' else f"missed/{s['invalidation']}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _baseline_spec(cfg):
    """(stop_mode, target_fn) of the strategy baseline.

    v1: breaker stop + 0.25xATR(H1), target 2R.
    v2: M1 pullback-extreme stop + 0.1xATR(M1), target post-break extreme.
    """
    if cfg.strategy == 'v2':
        return 'pullback', lambda s: ('price', s.get('target_price'))
    return 'breaker', lambda s: ('r', cfg.baseline_target_r)


def _trades(m15, entered, cfg, stop_mode, target_fn):
    trades = []
    for s in entered:
        tgt = target_fn(s)
        if tgt[0] == 'price' and tgt[1] is None:
            continue
        t = simulate_trade(_sim_candles(s, m15), s, stop_mode, tgt, cfg)
        if t:
            trades.append(t)
    return trades


def run_diagnose(h1, m15, cfg, setups=None):
    split = int(len(h1) * cfg.train_fraction)
    if setups is None:
        raise ValueError('run_diagnose expects precomputed setups')
    train = [s for s in setups if s['breaker_index'] < split]
    entered = [s for s in train if s['entered']]
    base_stop, base_target = _baseline_spec(cfg)

    result = {
        'mode': 'diagnose',
        'strategy': cfg.strategy,
        'train_fraction': cfg.train_fraction,
        'h1_candles_total': len(h1),
        'train_h1_candles': split,
        'setups_train': len(train),
        'outcomes': _count_outcomes(train),
        'entered': len(entered),
    }

    # Q1: pullback depth distributions
    result['pullback_depth_pct_impulse'] = _percentiles(
        [s.get('depth_pct_impulse') for s in entered])
    result['pullback_depth_pct_zone'] = _percentiles(
        [s.get('depth_pct_zone') for s in entered])

    # Q2: target comparison (baseline stop, all entered setups)
    targets = {'1.5R': ('r', 1.5), '2R': ('r', 2.0), '3R': ('r', 3.0)}
    result['targets'] = {}
    for name, tgt in targets.items():
        trades = _trades(m15, entered, cfg, base_stop, lambda s, t=tgt: t)
        result['targets'][name] = trade_stats(trades)
    trades = _trades(m15, entered, cfg, base_stop,
                     lambda s: ('price', s.get('post_extreme')))
    result['targets']['post_break_extreme'] = trade_stats(trades)

    # Q3: stop variants at the strategy baseline target
    result['stop_variants'] = {}
    for stop_mode in ('breaker', 'pullback'):
        trades = _trades(m15, entered, cfg, stop_mode, base_target)
        result['stop_variants'][stop_mode] = trade_stats(trades)

    # Q4: missed trades
    missed = [s for s in train if s['outcome'] == 'missed']
    invalid = [s for s in train if s['outcome'] == 'invalid']
    tracked = [s for s in train if s['outcome'] in ('missed', 'entered', 'invalid')]
    by_reason = {}
    for s in missed:
        by_reason[s['invalidation']] = by_reason.get(s['invalidation'], 0) + 1
    result['missed'] = {
        'by_reason': by_reason,
        'invalid_before_pullback': sum(
            1 for s in invalid
            if (s['invalidation'] or '').endswith('_before_pullback')),
        'invalid_during_pullback': len(invalid) - sum(
            1 for s in invalid
            if (s['invalidation'] or '').endswith('_before_pullback')),
        'missed_total': len(missed) + len(invalid),
        'tracked_total': len(tracked),
        'missed_quote': round((len(missed) + len(invalid)) / len(tracked), 4)
                        if tracked else None,
    }

    # Baseline trade list for inspection
    result['baseline_trades'] = _trades(m15, entered, cfg, base_stop, base_target)
    base = result['baseline_trades']

    # Loss anatomy (explains avg_loss_r > 1): losses by exit reason + the
    # cost share in R. Fees+slippage are charged on NOTIONAL, so in R terms
    # they scale with entry/risk — tight stops make even small bps a large
    # fraction of R (v2 warning, spec: measured, not hoped).
    result['loss_breakdown'] = _loss_breakdown(base, cfg)

    # Distribution of winning R multiples
    result['win_r_distribution'] = _percentiles(
        [t['r_net'] for t in base if t['win']])

    # Split by direction and calendar half-year (is a window atypical?)
    result['by_direction'] = {
        d: trade_stats([t for t in base if t['direction'] == d])
        for d in ('long', 'short')}
    half = {}
    for t in base:
        dt = datetime.fromtimestamp(t['entry_ts'] / 1000, tz=timezone.utc)
        key = f"{dt.year}-H{1 if dt.month <= 6 else 2}"
        half.setdefault(key, []).append(t)
    result['by_halfyear'] = {k: trade_stats(v) for k, v in sorted(half.items())}

    # Cost sensitivity: same setups, same baseline, three cost profiles.
    # Setup/entry detection is cost-independent, so only trades are re-simulated.
    result['cost_sensitivity'] = {}
    for fee, slip in ((2.0, 5.0), (5.0, 5.0), (10.0, 10.0)):
        label = f'{fee:g}+{ slip:g}'
        cfg2 = replace(cfg, fee_bps=fee, slippage_bps=slip)
        trades2 = _trades(m15, entered, cfg2, base_stop, base_target)
        result['cost_sensitivity'][label] = {
            'baseline': trade_stats(trades2),
            'loss_breakdown': _loss_breakdown(trades2, cfg2),
        }
    return result


def _loss_breakdown(trades, cfg):
    """Losses grouped by exit reason, with the estimated cost share in R."""
    out = {}
    losses = [t for t in trades if not t['win']]
    groups = {'all': losses}
    for reason in ('stop', 'timeout'):
        groups[reason] = [t for t in losses if t['exit_reason'] == reason]
    for name, sub in groups.items():
        if not sub:
            out[name] = {'n': 0}
            continue
        cost_rs = []
        for t in sub:
            risk = abs(t['entry'] - t['stop'])
            if risk > 0:
                cost_rs.append(cfg.cost_frac * (t['entry'] + t['exit']) / risk)
        out[name] = {
            'n': len(sub),
            'avg_r_net': round(sum(t['r_net'] for t in sub) / len(sub), 4),
            'avg_cost_r': (round(sum(cost_rs) / len(cost_rs), 4)
                           if cost_rs else None),
        }
    return out


def run_validate(h1, m15, cfg, setups=None):
    """ONE config (strategy baseline), ONE OOS run."""
    if setups is None:
        raise ValueError('run_validate expects precomputed setups')
    split = int(len(h1) * cfg.train_fraction)
    oos = [s for s in setups if s['breaker_index'] >= split]
    entered = [s for s in oos if s['entered']]
    base_stop, base_target = _baseline_spec(cfg)
    trades = _trades(m15, entered, cfg, base_stop, base_target)
    stats = trade_stats(trades)

    kills = []
    if stats['n'] < 100:
        kills.append(f"n={stats['n']} < 100 trades")
    if stats['n'] and stats['expectancy_r'] <= 0:
        kills.append(f"net expectancy {stats['expectancy_r']}R <= 0")
    if stats['n'] and stats.get('breakeven_wr') is not None:
        lo, hi = stats['wilson95']
        if lo <= stats['breakeven_wr'] <= hi:
            kills.append(f"Wilson-CI [{lo}, {hi}] includes breakeven WR "
                         f"{stats['breakeven_wr']}")

    if cfg.strategy == 'v2':
        cfg_desc = {'stop': 'm1_pullback_extreme+0.1ATR(M1)',
                    'target': 'post_break_extreme'}
    else:
        cfg_desc = {'stop': 'breaker+0.25ATR', 'target': f'{cfg.baseline_target_r}R',
                    'pullback_filter': cfg.require_pullback_filter}
    return {
        'mode': 'validate',
        'strategy': cfg.strategy,
        'config': cfg_desc,
        'oos_h1_candles': len(h1) - split,
        'setups_oos': len(oos),
        'outcomes': _count_outcomes(oos),
        'stats': stats,
        'kill_criteria_triggered': kills,
        'verdict': 'FAIL' if kills else 'PASS',
        'trades': trades,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def render_md(result, cfg):
    lines = ['# Breaker Backtest — STRATEGY_V1', '']
    lines.append(f"Mode: **{result['mode']}** | Strategy: **{cfg.strategy}** | "
                 f"TFs: {cfg.setup_tf}/{cfg.exec_tf} | "
                 f"Costs: {cfg.fee_bps}+{cfg.slippage_bps} bps/side | "
                 f"Train-Fraction: {cfg.train_fraction}")
    lines.append('')
    if result['mode'] == 'diagnose':
        lines.append(f"H1 candles: {result['h1_candles_total']} "
                     f"(train: {result['train_h1_candles']})")
        lines.append(f"Setups (train): {result['setups_train']}, "
                     f"entered: {result['entered']}")
        lines.append(f"Outcomes: `{json.dumps(result['outcomes'])}`")
        lines.append('')
        lines.append('## Pullback depth')
        for key in ('pullback_depth_pct_impulse', 'pullback_depth_pct_zone'):
            lines.append(f"- {key}: `{json.dumps(result[key])}`")
        lines.append('')
        lines.append('## Target comparison (baseline stop)')
        for name, st in result['targets'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append('## Stop variants (baseline target)')
        for name, st in result['stop_variants'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append(f"## Missed trades\n`{json.dumps(result['missed'])}`")
        lines.append('')
        lines.append('## Loss anatomy (baseline)')
        lines.append(f"`{json.dumps(result['loss_breakdown'])}`")
        lines.append('')
        lines.append('## Win-R distribution (baseline)')
        lines.append(f"`{json.dumps(result['win_r_distribution'])}`")
        lines.append('')
        lines.append('## By direction (baseline)')
        for name, st in result['by_direction'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append('## By half-year (baseline)')
        for name, st in result['by_halfyear'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append('## Cost sensitivity (baseline config)')
        for label, block in result['cost_sensitivity'].items():
            lines.append(f"- {label} bps: `{json.dumps(block['baseline'])}`")
    else:
        st = result['stats']
        lines.append(f"OOS H1 candles: {result['oos_h1_candles']}, "
                     f"setups: {result['setups_oos']}")
        lines.append(f"Stats: `{json.dumps(st)}`")
        lines.append(f"Outcomes: `{json.dumps(result['outcomes'])}`")
        lines.append('')
        if result['kill_criteria_triggered']:
            lines.append('## Kill criteria TRIGGERED')
            for k in result['kill_criteria_triggered']:
                lines.append(f'- {k}')
        lines.append(f"\n## Verdict: **{result['verdict']}**")
    lines.append('')
    return '\n'.join(lines)


def _date_to_ms(s):
    dt = datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def main(argv=None):
    p = argparse.ArgumentParser(description='Breaker Zone Backtest (STRATEGY_V1)')
    p.add_argument('--db', required=True, help='SQLite DB path (opened read-only)')
    p.add_argument('--mode', choices=['diagnose', 'validate'], required=True)
    p.add_argument('--strategy', choices=['v1', 'v2'], default='v2',
                   help='v1: exec-tf close-out entry, 2R, breaker stop | '
                        'v2: M1 BOS trigger, M1 stop, post-break extreme target')
    p.add_argument('--setup-tf', default='1h', choices=sorted(TF_MS),
                   help='timeframe of break detection (default: 1h)')
    p.add_argument('--exec-tf', default='15m', choices=sorted(TF_MS),
                   help='timeframe of pullback tracking / trade sim (default: 15m)')
    p.add_argument('--validity', type=int, default=None,
                   help='setup validity in setup-tf candles '
                        '(default: 48 for 1h, 24 otherwise)')
    p.add_argument('--since', help='Start date YYYY-MM-DD (default: all)')
    p.add_argument('--until', help='End date YYYY-MM-DD (default: all)')
    p.add_argument('--json-out', help='Write full results as JSON')
    p.add_argument('--md-out', help='Write summary report as Markdown')
    p.add_argument('--fee-bps', type=float, default=2.0)
    p.add_argument('--slippage-bps', type=float, default=5.0)
    p.add_argument('--train-fraction', type=float, default=0.65)
    args = p.parse_args(argv)

    cfg = Config(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
                 train_fraction=args.train_fraction, strategy=args.strategy,
                 setup_tf=args.setup_tf, exec_tf=args.exec_tf,
                 validity_h1=(args.validity if args.validity is not None
                              else (48 if args.setup_tf == '1h' else 24)))

    since_ms = _date_to_ms(args.since) if args.since else 0
    until_ms = _date_to_ms(args.until) if args.until else 2**62

    # READ-ONLY: the productive DB must never be opened for writing.
    conn = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        h1 = load_candles(conn, args.setup_tf, since_ms, until_ms)
        m15 = load_candles(conn, args.exec_tf, since_ms, until_ms)

        print(f'Loaded {len(h1):,} {args.setup_tf} + {len(m15):,} {args.exec_tf} candles')
        if not h1 or not m15:
            print('ERROR: no candle data in range', file=sys.stderr)
            return 1

        # v2 loads M1 lazily per setup window instead of all 792k rows at
        # once: one small read-only query per touched setup.
        m1_loader = None
        if cfg.strategy == 'v2':
            cols = ', '.join(CANDLE_COLS)
            def m1_loader(start_ms, end_ms):
                rows = conn.execute(
                    f'SELECT {cols} FROM candles_1m '
                    'WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp',
                    (start_ms, end_ms)).fetchall()
                return [dict(r) for r in rows]

        setups = compute_setups(h1, m15, cfg, m1_loader)
        print(f'Setups tracked: {len(setups):,} '
              f'(entered: {sum(1 for s in setups if s["entered"]):,})')

        if args.mode == 'diagnose':
            result = run_diagnose(h1, m15, cfg, setups=setups)
        else:
            result = run_validate(h1, m15, cfg, setups=setups)
    finally:
        conn.close()

    result['config'] = asdict(cfg)
    result['range'] = {'since': args.since, 'until': args.until}

    if args.json_out:
        with open(args.json_out, 'w') as f:
            json.dump(result, f, indent=1)
        print(f'JSON -> {args.json_out}')
    md = render_md(result, cfg)
    if args.md_out:
        with open(args.md_out, 'w') as f:
            f.write(md)
        print(f'MD   -> {args.md_out}')
    print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
