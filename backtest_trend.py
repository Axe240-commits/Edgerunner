#!/usr/bin/env python3
"""
Continuation Backtest — the anti-philosophy card.

Five measurements proved mean-reversion after structure breaks is real but
priced in (~0R gross on H4). This card plays the OTHER side: the 12-20% of
H4 breaks that never reclaim. No retest, no fade — enter WITH the break.

Rules:
  - Setup: H4 BOS (bos_bull/bos_bear + break_depth > 0, same detection as
    the breaker/flip backtests). bos_bull -> LONG, bos_bear -> SHORT.
  - Entry: close of the break candle (immediate, no validity window).
  - Stop: 1.5 x ATR(14, setup tf) behind entry — volatility-based, NOT at
    the breaker extreme (avoids the kill-direct cost-R explosion where the
    stop sits a few ticks from entry).
  - Targets: 2R / 3R / 5R + Chandelier trail (3 x ATR from the highest
    exec-tf high since entry, long; mirrored).
  - Trade sim on exec tf (default 1h): same-bar conflict = stop, timeout
    max_hold exec bars mark-to-market, costs via CLI (default 5+5 bps).

Point-in-time: the exec scan starts strictly after the break candle's close.

The DB is opened READ-ONLY. Reports go to --json-out / --md-out only.
"""
import argparse
import json
import sqlite3
import sys
from bisect import bisect_left
from dataclasses import asdict, replace
from datetime import datetime, timezone

# Reuse the proven breaker machinery (trade sim, stats, costs).
from backtest_breaker import (
    Config, simulate_trade, trade_stats, wilson_ci,  # noqa: F401
    _percentiles, _loss_breakdown, _count_outcomes, _sim_candles,
    _date_to_ms, TF_MS, collect_run_meta,
)

TREND_COLS = ('timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta',
              'delta_pct', 'vol_vs_ma', 'atr14', 'bos_bull', 'bos_bear',
              'break_depth')


def load_candles(conn, tf, since_ms, until_ms):
    table = f'candles_{tf}'
    cols = ', '.join(TREND_COLS)
    sql = (f'SELECT {cols} FROM {table} '
           'WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp')
    return [dict(r) for r in conn.execute(sql, (since_ms, until_ms)).fetchall()]


# ---------------------------------------------------------------------------
# Setups
# ---------------------------------------------------------------------------

def run_continuations(setup_candles, exec_candles, cfg):
    """Every H4 BOS becomes an immediate continuation trade candidate."""
    exec_ts = [c['timestamp'] for c in exec_candles]
    setup_ms = TF_MS[cfg.setup_tf]
    setups = []
    for i, b in enumerate(setup_candles):
        if (b.get('break_depth') or 0) <= 0:
            continue
        if b.get('bos_bull') == 1:
            direction = 'long'
        elif b.get('bos_bear') == 1:
            direction = 'short'
        else:
            continue
        atr = b.get('atr14') or 0.0
        if atr <= 0:
            setups.append({'direction': direction, 'breaker_ts': b['timestamp'],
                           'breaker_index': i, 'outcome': 'skipped',
                           'invalidation': 'missing_atr', 'entered': False})
            continue
        long_ = direction == 'long'
        entry = b['close']
        stop = entry - cfg.trend_stop_atr_mult * atr if long_ \
            else entry + cfg.trend_stop_atr_mult * atr
        entry_idx = bisect_left(exec_ts, b['timestamp'] + setup_ms) - 1
        setups.append({
            'direction': direction,
            'strategy': 'continuation',
            'breaker_ts': b['timestamp'],
            'breaker_index': i,
            'atr': atr,
            'outcome': 'entered',
            'entered': True,
            'entry_ts': b['timestamp'] + setup_ms,
            'entry_price': entry,
            'entry_idx': entry_idx,
            'pullback_stop': stop,   # simulate_trade stop_mode 'pullback'
            'breaker_stop': stop,
            'max_hold': cfg.max_hold_bars,
        })
    return setups


# ---------------------------------------------------------------------------
# Chandelier trail simulation (no fixed target)
# ---------------------------------------------------------------------------

def simulate_trail(candles, setup, cfg, trail_mult=3.0):
    """Chandelier exit: trail = extreme since entry -/+ trail_mult x ATR.

    Conservative ordering (documented): within each bar the exit is checked
    FIRST against the trail computed from PREVIOUS bars only; only then is
    the extreme advanced for the next bar. The running bar's own high/low
    never tightens the trail it is checked against (no intrabar future).
    The active stop is the better of (initial stop, trail). Exits at the
    trail/stop price or mark-to-market at timeout. R is measured against the
    INITIAL stop distance.
    """
    long_ = setup['direction'] == 'long'
    entry = setup['entry_price']
    init_stop = setup['pullback_stop']
    risk = abs(entry - init_stop)
    if risk <= 0:
        return None
    atr = setup['atr']
    cf = cfg.cost_frac
    entry_eff = entry * (1 + cf) if long_ else entry * (1 - cf)

    start = setup['entry_idx'] + 1
    end = min(start + setup.get('max_hold', cfg.max_hold_bars), len(candles))
    extreme = None
    exit_price = None
    reason = 'timeout'
    bars_held = 0
    for k in range(start, end):
        c = candles[k]
        bars_held += 1
        # 1) exit check against the OLD trail (previous bars only)
        if extreme is not None:
            trail = extreme - trail_mult * atr if long_ \
                else extreme + trail_mult * atr
            active = max(init_stop, trail) if long_ else min(init_stop, trail)
        else:
            active = init_stop
        if long_ and c['low'] <= active:
            exit_price, reason = active, 'trail'
            break
        if not long_ and c['high'] >= active:
            exit_price, reason = active, 'trail'
            break
        # 2) only now advance the extreme for the NEXT bar
        extreme = (c['high'] if extreme is None else max(extreme, c['high'])) \
            if long_ else \
            (c['low'] if extreme is None else min(extreme, c['low']))
    if exit_price is None and end > start:
        exit_price = candles[end - 1]['close']
    if exit_price is None:
        return None

    exit_eff = exit_price * (1 - cf) if long_ else exit_price * (1 + cf)
    r_net = ((exit_eff - entry_eff) if long_ else (entry_eff - exit_eff)) / risk
    return {
        'direction': setup['direction'],
        'entry_ts': setup['entry_ts'],
        'entry': entry,
        'stop': init_stop,
        'target': None,
        'exit': exit_price,
        'exit_reason': reason,
        'bars_held': bars_held,
        'r_net': r_net,
        'win': r_net > 0,
    }


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def _trades(m15, entered, cfg, target_fn):
    trades = []
    for s in entered:
        tgt = target_fn(s)
        t = simulate_trade(_sim_candles(s, m15), s, 'pullback', tgt, cfg)
        if t:
            trades.append(t)
    return trades


def run_diagnose(setup_candles, exec_candles, cfg, setups):
    split = int(len(setup_candles) * cfg.train_fraction)
    train = [s for s in setups if s['breaker_index'] < split]
    entered = [s for s in train if s['entered']]

    result = {
        'mode': 'diagnose',
        'strategy': 'continuation',
        'train_fraction': cfg.train_fraction,
        'setup_candles_total': len(setup_candles),
        'train_candles': split,
        'breaks_train': len(train),
        'outcomes': _count_outcomes(train),
        'entered': len(entered),
    }

    result['targets'] = {}
    for name, tgt in {'2R': ('r', 2.0), '3R': ('r', 3.0), '5R': ('r', 5.0)}.items():
        trades = _trades(exec_candles, entered, cfg, lambda s, t=tgt: t)
        result['targets'][name] = trade_stats(trades)
    trail_trades = [t for s in entered
                    if (t := simulate_trail(exec_candles, s, cfg))]
    result['targets']['chandelier_3atr'] = trade_stats(trail_trades)

    # Baseline = 3R (middle target)
    base = _trades(exec_candles, entered, cfg, lambda s: ('r', 3.0))
    result['baseline_trades'] = base
    result['loss_breakdown'] = _loss_breakdown(base, cfg)

    result['by_direction'] = {
        d: trade_stats([t for t in base if t['direction'] == d])
        for d in ('long', 'short')}
    half = {}
    for t in base:
        dt = datetime.fromtimestamp(t['entry_ts'] / 1000, tz=timezone.utc)
        key = f"{dt.year}-H{1 if dt.month <= 6 else 2}"
        half.setdefault(key, []).append(t)
    result['by_halfyear'] = {k: trade_stats(v) for k, v in sorted(half.items())}

    result['cost_sensitivity'] = {}
    for fee, slip in ((2.0, 5.0), (5.0, 5.0), (10.0, 10.0)):
        label = f'{fee:g}+{slip:g}'
        cfg2 = replace(cfg, fee_bps=fee, slippage_bps=slip)
        trades2 = _trades(exec_candles, entered, cfg2, lambda s: ('r', 3.0))
        result['cost_sensitivity'][label] = {
            'baseline': trade_stats(trades2),
            'loss_breakdown': _loss_breakdown(trades2, cfg2),
        }
    return result


def run_validate(setup_candles, exec_candles, cfg, setups):
    """ONE config (3R baseline), ONE OOS run."""
    split = int(len(setup_candles) * cfg.train_fraction)
    oos = [s for s in setups if s['breaker_index'] >= split]
    entered = [s for s in oos if s['entered']]
    trades = _trades(exec_candles, entered, cfg, lambda s: ('r', 3.0))
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

    return {
        'mode': 'validate',
        'strategy': 'continuation',
        'config': {'stop': '1.5xATR(setup)', 'target': '3R'},
        'oos_candles': len(setup_candles) - split,
        'breaks_oos': len(oos),
        'outcomes': _count_outcomes(oos),
        'stats': stats,
        'kill_criteria_triggered': kills,
        'verdict': 'FAIL' if kills else 'PASS',
        'trades': trades,
    }


# ---------------------------------------------------------------------------
# Report / CLI
# ---------------------------------------------------------------------------

def render_md(result, cfg):
    lines = ['# Continuation Backtest (anti mean-reversion card)', '']
    lines.append(f"Mode: **{result['mode']}** | TFs: {cfg.setup_tf}/{cfg.exec_tf} | "
                 f"Costs: {cfg.fee_bps}+{cfg.slippage_bps} bps/side | "
                 f"Train-Fraction: {cfg.train_fraction}")
    lines.append('')
    if result['mode'] == 'diagnose':
        lines.append(f"Breaks (train): {result['breaks_train']}, "
                     f"entered: {result['entered']}")
        lines.append(f"Outcomes: `{json.dumps(result['outcomes'])}`")
        lines.append('')
        lines.append('## Target comparison')
        for name, st in result['targets'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append(f"## Loss anatomy (baseline 3R)\n`{json.dumps(result['loss_breakdown'])}`")
        lines.append('')
        lines.append('## By direction (baseline 3R)')
        for name, st in result['by_direction'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append('## By half-year (baseline 3R)')
        for name, st in result['by_halfyear'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append('## Cost sensitivity (baseline 3R)')
        for label, block in result['cost_sensitivity'].items():
            lines.append(f"- {label} bps: `{json.dumps(block['baseline'])}`")
    else:
        st = result['stats']
        lines.append(f"OOS candles: {result['oos_candles']}, breaks: {result['breaks_oos']}")
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


def main(argv=None):
    p = argparse.ArgumentParser(description='Continuation Backtest (H4 BOS, no fade)')
    p.add_argument('--db', required=True, help='SQLite DB path (opened read-only)')
    p.add_argument('--mode', choices=['diagnose', 'validate'], required=True)
    p.add_argument('--setup-tf', default='4h', choices=sorted(TF_MS),
                   help='timeframe of break detection (default: 4h)')
    p.add_argument('--exec-tf', default='1h', choices=sorted(TF_MS),
                   help='timeframe of trade sim (default: 1h)')
    p.add_argument('--stop-atr', type=float, default=1.5,
                   help='stop distance in ATR(setup) behind entry (default: 1.5)')
    p.add_argument('--since', help='Start date YYYY-MM-DD (default: all)')
    p.add_argument('--until', help='End date YYYY-MM-DD (default: all)')
    p.add_argument('--json-out', help='Write full results as JSON')
    p.add_argument('--md-out', help='Write summary report as Markdown')
    p.add_argument('--fee-bps', type=float, default=5.0)
    p.add_argument('--slippage-bps', type=float, default=5.0)
    p.add_argument('--train-fraction', type=float, default=0.65)
    args = p.parse_args(argv)

    cfg = Config(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
                 train_fraction=args.train_fraction,
                 setup_tf=args.setup_tf, exec_tf=args.exec_tf)
    cfg.trend_stop_atr_mult = args.stop_atr

    since_ms = _date_to_ms(args.since) if args.since else 0
    until_ms = _date_to_ms(args.until) if args.until else 2**62

    # READ-ONLY: the productive DB must never be opened for writing.
    conn = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        setup_candles = load_candles(conn, args.setup_tf, since_ms, until_ms)
        exec_candles = load_candles(conn, args.exec_tf, since_ms, until_ms)
        run_meta = collect_run_meta(
            'backtest_trend.py', sys.argv, cfg, args.db, conn,
            [f'candles_{args.setup_tf}', f'candles_{args.exec_tf}'])
    finally:
        conn.close()

    print(f'Loaded {len(setup_candles):,} {args.setup_tf} + '
          f'{len(exec_candles):,} {args.exec_tf} candles')
    if not setup_candles or not exec_candles:
        print('ERROR: no candle data in range', file=sys.stderr)
        return 1

    setups = run_continuations(setup_candles, exec_candles, cfg)
    print(f'Continuation setups: {len(setups):,}')

    if args.mode == 'diagnose':
        result = run_diagnose(setup_candles, exec_candles, cfg, setups)
    else:
        result = run_validate(setup_candles, exec_candles, cfg, setups)

    result['config'] = asdict(cfg)
    result['config']['trend_stop_atr_mult'] = cfg.trend_stop_atr_mult
    result['run_meta'] = run_meta
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
