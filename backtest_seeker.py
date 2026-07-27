#!/usr/bin/env python3
"""
Seeker Kill + Retest Backtest — the native seeker-event variant of the
failed-breakout law (breaker/flip families died; H4 flip landed at 0R gross
with only 0.2R cost drag. Fallen quote: 80-88% of breaks fail).

SEEKER SEMANTICS (verified in candle_analyzer.py, not guessed):
  - Seeker HS (`is_seeker_hs`): candle at a swing HIGH whose upper wick is
    >= 20% of its range — a liquidity sweep above the high that was rejected.
    Wick zone = [body_high, high] (`seeker_zone_bottom/top`).
    Seeker LS: mirrored at swing lows, zone = [low, body_low].
  - Seeker div (`is_seeker_div*`): a later candle touches the zone AND pushes
    its body beyond the seeker's body edge — a continuation attempt into the
    zone. `killed_seeker_divs` counts divs of the killed seeker(s).
  - Seeker kill (`is_seeker_kill`): a candle CLOSES beyond the seeker extreme
    (HS kill: close > seeker.high). On the kill candle the `seeker_zone_*`
    columns carry the KILLED seeker's zone and `killed_seeker_ts` its origin
    timestamp (representative: the killed seeker with the most divs).

TRADE THESIS (SHORT on a killed HS seeker, LONG mirrored):
  The kill closes ABOVE the sweep high — breakout buyers above the old high
  are now trapped if the move fails. Variant A (retest): price comes back
  into the seeker zone on 1h; a 1h candle trading in the zone and closing
  below its bottom = entry (breaker-v1-style close-out). Variant B (reclaim
  style): entry directly at the kill candle close, no retest.
  Stop: seeker high (zone_top) + 0.25 x ATR(14, setup tf).
  Targets: 1.5R/2R/3R + opposite swing extreme (last swing low before kill).
  Validity: 24 setup-tf candles. Point-in-time: exec scan starts strictly
  after the kill candle's close.

The DB is opened READ-ONLY. Reports go to --json-out / --md-out only.
"""
import argparse
import json
import os
import sqlite3
import sys
from bisect import bisect_left
from dataclasses import asdict, replace
from datetime import datetime, timezone

# Reuse the proven breaker machinery (DB access, trade sim, stats, costs).
from backtest_breaker import (
    Config, simulate_trade, trade_stats, wilson_ci,  # noqa: F401
    _percentiles, _loss_breakdown, _count_outcomes, _sim_candles,
    _date_to_ms, TF_MS, collect_run_meta,
)

SEEKER_COLS = ('timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta',
               'delta_pct', 'vol_vs_ma', 'atr14',
               'is_seeker_hs', 'is_seeker_ls', 'is_seeker_kill',
               'killed_seeker_ts', 'killed_seeker_divs', 'killed_seekers_count',
               'seeker_zone_top', 'seeker_zone_bottom',
               'is_swing_high', 'is_swing_low')


def load_candles(conn, tf, since_ms, until_ms):
    table = f'candles_{tf}'
    cols = ', '.join(SEEKER_COLS)
    sql = (f'SELECT {cols} FROM {table} '
           'WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp')
    return [dict(r) for r in conn.execute(sql, (since_ms, until_ms)).fetchall()]


# ---------------------------------------------------------------------------
# Setup tracking
# ---------------------------------------------------------------------------

def _opposite_swing_target(setup, i, direction):
    """Last swing extreme BEFORE the kill candle (range-rotation target)."""
    if direction == 'short':
        return next((setup[j]['low'] for j in range(i - 1, -1, -1)
                     if setup[j].get('is_swing_low') == 1), None)
    return next((setup[j]['high'] for j in range(i - 1, -1, -1)
                 if setup[j].get('is_swing_high') == 1), None)


def track_kill(setup_tf_candles, i, direction, exec_candles, exec_ts, cfg):
    """Track one seeker kill on exec-tf bars (variant A: retest entry).

    direction='short': killed HS seeker (close above sweep high -> fade).
    direction='long':  killed LS seeker (close below sweep low -> fade up).
    """
    b = setup_tf_candles[i]
    short = direction == 'short'
    atr = b.get('atr14') or 0.0
    zone_top = b.get('seeker_zone_top') or 0.0
    zone_bottom = b.get('seeker_zone_bottom') or 0.0

    rec = {
        'direction': direction,
        'strategy': 'seeker_kill',
        'breaker_ts': b['timestamp'],   # kill candle ts (kept key name)
        'breaker_index': i,
        'killed_seeker_ts': b.get('killed_seeker_ts') or 0,
        'killed_seeker_divs': b.get('killed_seeker_divs') or 0,
        'zone_top': zone_top,
        'zone_bottom': zone_bottom,
        'seeker_extreme': zone_top if short else zone_bottom,
        'atr': atr,
        'outcome': None,
        'invalidation': None,
        'entered': False,
    }
    if atr <= 0 or zone_top <= zone_bottom or zone_bottom <= 0:
        rec['outcome'] = 'skipped'
        rec['invalidation'] = 'missing_atr_or_zone'
        return rec

    rec['swing_target'] = _opposite_swing_target(setup_tf_candles, i, direction)
    buf = cfg.stop_atr_mult * atr
    stop = (rec['seeker_extreme'] + buf) if short else (rec['seeker_extreme'] - buf)
    rec['pullback_stop'] = stop
    rec['breaker_stop'] = stop  # only one stop variant here

    setup_ms = TF_MS[cfg.setup_tf]
    end_j = min(i + cfg.validity_h1, len(setup_tf_candles) - 1)
    incomplete = end_j < i + cfg.validity_h1

    # --- Variant B (reclaim style): entry directly at the kill close.
    # Guard (documented rule): the direct entry is only executable when the
    # stop actually PROTECTS — i.e. it sits on the correct side of the entry
    # (short: above, long: below) AND the stop distance covers 2x round-trip
    # costs in price (4 x cost_frac x entry). An ATR-based floor is
    # deliberately NOT used: a fade at kill close always sits between the
    # extreme and extreme+buffer, so any ATR floor >= buffer would
    # structurally exclude every direct entry.
    entry_b = b['close']
    risk_b = abs(entry_b - stop)
    min_risk = 4.0 * cfg.cost_frac * entry_b
    protective = ((short and stop > entry_b)
                  or (not short and stop < entry_b)) and risk_b >= min_risk
    # Simulated separately from exec bars strictly after the kill close.
    first_k = bisect_left(exec_ts, b['timestamp'] + setup_ms)
    if protective:
        rec['variant_b'] = {
            'entry_idx': first_k - 1,   # sim starts at first_k (entry bar "before")
            'entry_ts': b['timestamp'] + setup_ms,
            'entry_price': entry_b,
            'max_hold': cfg.max_hold_bars,
            'direction': direction,
            'pullback_stop': stop,
            'breaker_stop': stop,
            'swing_target': rec['swing_target'],
        }
    else:
        rec['variant_b'] = None
        rec['variant_b_skip'] = 'non_protective_stop'

    # --- Variant A: retest of the seeker zone, close-out entry.
    retest_started = False
    post_extreme = None
    for j in range(i + 1, end_j + 1):
        win_start = setup_tf_candles[j]['timestamp']
        lo = bisect_left(exec_ts, win_start)
        hi = bisect_left(exec_ts, win_start + setup_ms)
        for k in range(lo, hi):
            c = exec_candles[k]
            if short:
                post_extreme = (c['high'] if post_extreme is None
                                else max(post_extreme, c['high']))
                if not retest_started:
                    if c['low'] <= zone_top:   # back down into the zone
                        retest_started = True
                # Entry: trades IN the zone (low <= zone_top) and closes
                # bearish OUT of it (close < zone_bottom).
                if retest_started and c['low'] <= zone_top \
                        and c['close'] < zone_bottom:
                    rec['entered'] = True
                    rec['entry_idx'] = k
                    break
            else:
                post_extreme = (c['low'] if post_extreme is None
                                else min(post_extreme, c['low']))
                if not retest_started:
                    if c['high'] >= zone_bottom:
                        retest_started = True
                if retest_started and c['high'] >= zone_bottom \
                        and c['close'] > zone_top:
                    rec['entered'] = True
                    rec['entry_idx'] = k
                    break
        if rec['entered']:
            break

    rec['post_extreme'] = post_extreme
    if rec['entered']:
        c = exec_candles[rec['entry_idx']]
        rec['outcome'] = 'entered'
        rec['entry_ts'] = c['timestamp']
        rec['entry_price'] = c['close']
        rec['max_hold'] = cfg.max_hold_bars
        rec['retest_bars'] = rec['entry_idx'] - first_k + 1
    elif incomplete:
        rec['outcome'] = 'incomplete'
    else:
        rec['outcome'] = 'missed'
        rec['invalidation'] = ('no_retest' if not retest_started
                               else 'no_exit_close')
    return rec


def run_seeker_kills(setup_candles, exec_candles, cfg):
    """Every seeker kill becomes a fade candidate in the opposite direction.

    Direction comes from the killed seeker's origin candle (looked up via
    killed_seeker_ts): HS killed -> short, LS killed -> long.
    """
    exec_ts = [c['timestamp'] for c in exec_candles]
    by_ts = {c['timestamp']: c for c in setup_candles}
    setups = []
    for i, b in enumerate(setup_candles):
        if b.get('is_seeker_kill') != 1:
            continue
        origin = by_ts.get(b.get('killed_seeker_ts') or 0)
        if origin is None:
            direction = None
        elif origin.get('is_seeker_hs') == 1:
            direction = 'short'
        elif origin.get('is_seeker_ls') == 1:
            direction = 'long'
        else:
            direction = None
        if direction is None:
            s = {'direction': 'unknown', 'breaker_ts': b['timestamp'],
                 'breaker_index': i, 'outcome': 'skipped',
                 'invalidation': 'origin_not_found', 'entered': False}
            setups.append(s)
            continue
        setups.append(track_kill(setup_candles, i, direction,
                                 exec_candles, exec_ts, cfg))
    return setups


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def _trades(m15, entries, cfg, target_fn):
    """entries: list of setup-like dicts with entry/stop fields set."""
    trades = []
    for s in entries:
        tgt = target_fn(s)
        if tgt[0] == 'price' and tgt[1] is None:
            continue
        t = simulate_trade(_sim_candles(s, m15), s, 'pullback', tgt, cfg)
        if t:
            trades.append(t)
    return trades


def _baseline_entries(train):
    """Baseline = variant A (retest)."""
    return [s for s in train if s['entered']]


def run_diagnose(setup_candles, exec_candles, cfg, setups):
    split = int(len(setup_candles) * cfg.train_fraction)
    train = [s for s in setups if s['breaker_index'] < split]
    entered = _baseline_entries(train)
    tracked = [s for s in train if s['outcome'] in ('entered', 'missed')]

    result = {
        'mode': 'diagnose',
        'strategy': 'seeker_kill',
        'train_fraction': cfg.train_fraction,
        'setup_candles_total': len(setup_candles),
        'train_candles': split,
        'kills_train': len(train),
        'outcomes': _count_outcomes(train),
        'entered': len(entered),
        'entry_quote': (round(len(entered) / len(tracked), 4)
                        if tracked else None),
        'retest_bars': _percentiles([s.get('retest_bars') for s in entered]),
    }

    # Variant A (retest): target comparison
    result['variant_a'] = {'targets': {}}
    for name, tgt in {'1.5R': ('r', 1.5), '2R': ('r', 2.0), '3R': ('r', 3.0)}.items():
        trades = _trades(exec_candles, entered, cfg, lambda s, t=tgt: t)
        result['variant_a']['targets'][name] = trade_stats(trades)
    trades = _trades(exec_candles, entered, cfg,
                     lambda s: ('price', s.get('swing_target')))
    result['variant_a']['targets']['opposite_swing'] = trade_stats(trades)

    # Variant B (reclaim style, entry at kill close)
    entries_b = [s['variant_b'] for s in train
                 if s.get('variant_b') and s['outcome'] != 'skipped']
    result['variant_b'] = {'targets': {}}
    for name, tgt in {'1.5R': ('r', 1.5), '2R': ('r', 2.0), '3R': ('r', 3.0)}.items():
        trades = _trades(exec_candles, entries_b, cfg, lambda s, t=tgt: t)
        result['variant_b']['targets'][name] = trade_stats(trades)
    trades = _trades(exec_candles, entries_b, cfg,
                     lambda s: ('price', s.get('swing_target')))
    result['variant_b']['targets']['opposite_swing'] = trade_stats(trades)

    # Variant B skip accounting (non-protective direct entries).
    result['variant_b_skipped'] = sum(
        1 for s in train if s.get('variant_b_skip') == 'non_protective_stop')

    # Baseline (variant A, 2R) deep stats
    base = _trades(exec_candles, entered, cfg, lambda s: ('r', 2.0))
    result['baseline_trades'] = base
    result['loss_breakdown'] = _loss_breakdown(base, cfg)

    # Kill+Div split: is the sharpest event (killed seeker had divs) better?
    result['by_killed_divs'] = {
        'divs>0': trade_stats(_trades(
            exec_candles, [s for s in entered if s['killed_seeker_divs'] > 0],
            cfg, lambda s: ('r', 2.0))),
        'divs=0': trade_stats(_trades(
            exec_candles, [s for s in entered if s['killed_seeker_divs'] == 0],
            cfg, lambda s: ('r', 2.0))),
    }

    result['by_direction'] = {
        d: trade_stats([t for t in base if t['direction'] == d])
        for d in ('long', 'short')}
    half = {}
    for t in base:
        dt = datetime.fromtimestamp(t['entry_ts'] / 1000, tz=timezone.utc)
        key = f"{dt.year}-H{1 if dt.month <= 6 else 2}"
        half.setdefault(key, []).append(t)
    result['by_halfyear'] = {k: trade_stats(v) for k, v in sorted(half.items())}

    # Cost sensitivity on the baseline (entries are cost-independent)
    result['cost_sensitivity'] = {}
    for fee, slip in ((2.0, 5.0), (5.0, 5.0), (10.0, 10.0)):
        label = f'{fee:g}+{slip:g}'
        cfg2 = replace(cfg, fee_bps=fee, slippage_bps=slip)
        trades2 = _trades(exec_candles, entered, cfg2, lambda s: ('r', 2.0))
        result['cost_sensitivity'][label] = {
            'baseline': trade_stats(trades2),
            'loss_breakdown': _loss_breakdown(trades2, cfg2),
        }
    return result


def run_validate(setup_candles, exec_candles, cfg, setups):
    """ONE config (variant A retest, 2R), ONE OOS run."""
    split = int(len(setup_candles) * cfg.train_fraction)
    oos = [s for s in setups if s['breaker_index'] >= split]
    entered = _baseline_entries(oos)
    trades = _trades(exec_candles, entered, cfg, lambda s: ('r', 2.0))
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
        'strategy': 'seeker_kill',
        'config': {'variant': 'A retest', 'stop': 'seeker_extreme+0.25ATR(setup)',
                   'target': '2R'},
        'oos_candles': len(setup_candles) - split,
        'kills_oos': len(oos),
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
    lines = ['# Seeker Kill + Retest Backtest', '']
    lines.append(f"Mode: **{result['mode']}** | TFs: {cfg.setup_tf}/{cfg.exec_tf} | "
                 f"Costs: {cfg.fee_bps}+{cfg.slippage_bps} bps/side | "
                 f"Train-Fraction: {cfg.train_fraction}")
    lines.append('')
    if result['mode'] == 'diagnose':
        lines.append(f"Kills (train): {result['kills_train']}, "
                     f"entered (variant A): {result['entered']}, "
                     f"entry quote: {result['entry_quote']}")
        lines.append(f"Outcomes: `{json.dumps(result['outcomes'])}`")
        lines.append(f"Retest speed (exec bars): `{json.dumps(result['retest_bars'])}`")
        lines.append('')
        for variant in ('variant_a', 'variant_b'):
            lines.append(f"## {variant} — targets")
            for name, st in result[variant]['targets'].items():
                lines.append(f"- {name}: `{json.dumps(st)}`")
            lines.append('')
        lines.append(f"## Kill+Div split (variant A, 2R)\n`{json.dumps(result['by_killed_divs'])}`")
        lines.append('')
        lines.append(f"## Loss anatomy (baseline)\n`{json.dumps(result['loss_breakdown'])}`")
        lines.append('')
        lines.append('## By direction (baseline)')
        for name, st in result['by_direction'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append('## By half-year (baseline)')
        for name, st in result['by_halfyear'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append('## Cost sensitivity (baseline)')
        for label, block in result['cost_sensitivity'].items():
            lines.append(f"- {label} bps: `{json.dumps(block['baseline'])}`")
    else:
        st = result['stats']
        lines.append(f"OOS candles: {result['oos_candles']}, kills: {result['kills_oos']}")
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
    p = argparse.ArgumentParser(description='Seeker Kill + Retest Backtest')
    p.add_argument('--db', required=True, help='SQLite DB path (opened read-only)')
    p.add_argument('--mode', choices=['diagnose', 'validate'], required=True)
    p.add_argument('--setup-tf', default='4h', choices=sorted(TF_MS),
                   help='timeframe of kill detection (default: 4h)')
    p.add_argument('--exec-tf', default='1h', choices=sorted(TF_MS),
                   help='timeframe of retest scan / trade sim (default: 1h)')
    p.add_argument('--validity', type=int, default=24,
                   help='setup validity in setup-tf candles (default: 24)')
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
                 setup_tf=args.setup_tf, exec_tf=args.exec_tf,
                 validity_h1=args.validity)

    since_ms = _date_to_ms(args.since) if args.since else 0
    until_ms = _date_to_ms(args.until) if args.until else 2**62

    # READ-ONLY: the productive DB must never be opened for writing.
    conn = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        setup_candles = load_candles(conn, args.setup_tf, since_ms, until_ms)
        exec_candles = load_candles(conn, args.exec_tf, since_ms, until_ms)
        run_meta = collect_run_meta(
            'backtest_seeker.py', sys.argv, cfg, args.db, conn,
            [f'candles_{args.setup_tf}', f'candles_{args.exec_tf}'])
    finally:
        conn.close()

    print(f'Loaded {len(setup_candles):,} {args.setup_tf} + '
          f'{len(exec_candles):,} {args.exec_tf} candles')
    if not setup_candles or not exec_candles:
        print('ERROR: no candle data in range', file=sys.stderr)
        return 1

    setups = run_seeker_kills(setup_candles, exec_candles, cfg)
    print(f'Seeker kills tracked: {len(setups):,} '
          f'(entered A: {sum(1 for s in setups if s["entered"]):,})')

    if args.mode == 'diagnose':
        result = run_diagnose(setup_candles, exec_candles, cfg, setups)
    else:
        result = run_validate(setup_candles, exec_candles, cfg, setups)

    result['config'] = asdict(cfg)
    result['run_meta'] = run_meta
    result['range'] = {'since': args.since, 'until': args.until}

    if args.json_out:
        with open(args.json_out, 'w') as f:
            json.dump(result, f, indent=1)
        print(f'JSON -> {args.json_out}')
        from evidence import build_evidence
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        split = int(len(setup_candles) * cfg.train_fraction)
        cutoff = (setup_candles[split]['timestamp']
                  if split < len(setup_candles) else None)
        ev_path = build_evidence(
            script='backtest_seeker.py', mode=args.mode, argv=sys.argv,
            cfg=cfg, db_path=args.db, result_path=args.json_out,
            tables=[f'candles_{args.setup_tf}', f'candles_{args.exec_tf}'],
            adapter_name='binance-futures',
            adapter_files=[os.path.join(repo_dir, 'hyperliquid_api.py'),
                           os.path.join(repo_dir, 'history_loader.py')],
            window={'since': args.since, 'until': args.until},
            train_cutoff_ts=cutoff)
        print(f'EVIDENCE -> {ev_path}')
    md = render_md(result, cfg)
    if args.md_out:
        with open(args.md_out, 'w') as f:
            f.write(md)
        print(f'MD   -> {args.md_out}')
    print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
