#!/usr/bin/env python3
"""
Fakeout-Flip Backtest (STRATEGY_FLIP_V1) — the other side of the breaker law.

The breaker family died on the finding that 75-83% of H1 breaks do NOT hold.
The flip plays exactly that side: when the pullback runs through the breaker
zone and RECLAIMS the broken swing level, the breakout was a trap; trapped
breakout traders fuel the counter-move.

Rules (STRATEGY_FLIP_V1.md, LONG on a failed bear break; SHORT mirrored):
  1. H1 closes beyond a swing level (bos_bear/bos_bull, break_depth > 0) —
     same detection as the breaker backtest. Break level = the broken swing
     level, reconstructed from the ATR-normalized break_depth
     (level = close +/- break_depth x atr14, documented in backtest_breaker).
  2. Post-break extreme = lowest M15 low after the break (the trap).
  3. Trigger: first M15 candle closing back ACROSS the break level
     (long: close above it). Entry = that candle's close.
  4. Stop: post-break extreme -/+ 0.1 x ATR(14, M15 of the reclaim candle).
  5. Baseline target 2R; alternatives 1.5R/3R/last H1 swing extreme before
     the break (range rotation).
  6. Validity: reclaim must come within 48 H1 after the break, else the
     breakout is real and the setup is dead.

Point-in-time: H1 features are used only after the H1 close; the M15 scan
starts strictly after the breaker candle's close. The reclaim candle's own
extreme counts toward the post-break extreme (conservative: wider stop).

The DB is opened READ-ONLY. Reports go to --json-out / --md-out only.

Usage:
    python3 backtest_flip.py --db edgerunner.db --mode diagnose \
        --json-out diag_flip.json --md-out diag_flip.md
"""
import argparse
import json
import os
import sqlite3
import sys
from bisect import bisect_left, bisect_right
from dataclasses import asdict, replace
from datetime import datetime, timezone

# Reuse the proven breaker machinery (DB access, trade sim, stats, costs).
from backtest_breaker import (
    Config, simulate_trade, trade_stats, wilson_ci,  # noqa: F401 (wilson re-exported)
    _percentiles, _loss_breakdown, _count_outcomes, _sim_candles,
    _date_to_ms, H1_MS, TF_MS, collect_run_meta,
)

# Flip needs the swing flags for the range-rotation target.
FLIP_COLS = ('timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta',
             'delta_pct', 'vol_vs_ma', 'delta_vs_ma', 'atr14',
             'bos_bull', 'bos_bear',
             'break_depth', 'is_swing_high', 'is_swing_low')


def load_candles(conn, tf, since_ms, until_ms):
    table = f'candles_{tf}'
    cols = ', '.join(FLIP_COLS)
    sql = (f'SELECT {cols} FROM {table} '
           'WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp')
    return [dict(r) for r in conn.execute(sql, (since_ms, until_ms)).fetchall()]


# ---------------------------------------------------------------------------
# Setup tracking
# ---------------------------------------------------------------------------

def _funding_at(funding, ts_ms):
    """Last funding print at or before ts_ms (point-in-time). None if none."""
    idx = bisect_right([f[0] for f in funding], ts_ms) - 1
    return funding[idx][1] if idx >= 0 else None


def track_flip(h1, i, flip_dir, m15, m15_ts, cfg, funding=None):
    """Track one H1 break for a reclaim of the broken level.

    flip_dir='long': failed bear break (bos_bear) -> reclaim above the level.
    flip_dir='short': failed bull break (bos_bull) -> close back below.
    """
    b = h1[i]
    atr_h1 = b.get('atr14') or 0.0
    depth = b.get('break_depth') or 0.0
    long_flip = flip_dir == 'long'
    # Break level = broken swing level (ATR-normalized break_depth, same
    # reconstruction as the breaker backtest).
    level = (b['close'] + depth * atr_h1 if long_flip
             else b['close'] - depth * atr_h1)

    rec = {
        'direction': flip_dir,
        'breaker_ts': b['timestamp'],
        'breaker_index': i,
        'break_level': level,
        'atr_h1': atr_h1,
        'outcome': None,
        'invalidation': None,
        'entered': False,
    }
    if atr_h1 <= 0:
        rec['outcome'] = 'skipped'
        rec['invalidation'] = 'missing_atr'
        return rec

    # Range-rotation target: last H1 swing extreme BEFORE the break.
    if long_flip:
        rec['swing_target'] = next(
            (h1[j]['high'] for j in range(i - 1, -1, -1)
             if h1[j].get('is_swing_high') == 1), None)
    else:
        rec['swing_target'] = next(
            (h1[j]['low'] for j in range(i - 1, -1, -1)
             if h1[j].get('is_swing_low') == 1), None)

    end_j = min(i + cfg.validity_h1, len(h1) - 1)
    incomplete = end_j < i + cfg.validity_h1
    first_k = bisect_left(m15_ts, h1[min(i + 1, len(h1) - 1)]['timestamp'])
    setup_ms = TF_MS[cfg.setup_tf]

    post_extreme = None
    for j in range(i + 1, end_j + 1):
        hour_start = h1[j]['timestamp']
        lo = bisect_left(m15_ts, hour_start)
        hi = bisect_left(m15_ts, hour_start + setup_ms)
        for k in range(lo, hi):
            c = m15[k]
            # The reclaim candle's own extreme counts toward the trap
            # (conservative: can only widen the stop).
            if long_flip:
                post_extreme = (c['low'] if post_extreme is None
                                else min(post_extreme, c['low']))
                if c['close'] > level:
                    rec['entered'] = True
                    rec['entry_idx'] = k
                    break
            else:
                post_extreme = (c['high'] if post_extreme is None
                                else max(post_extreme, c['high']))
                if c['close'] < level:
                    rec['entered'] = True
                    rec['entry_idx'] = k
                    break
        if rec['entered']:
            break

    if not rec['entered']:
        if incomplete:
            rec['outcome'] = 'incomplete'
        else:
            rec['outcome'] = 'missed'
            rec['invalidation'] = 'no_reclaim'  # breakout was real
        return rec

    c = m15[rec['entry_idx']]
    atr_m15 = c.get('atr14') or 0.0
    if atr_m15 <= 0:
        rec['outcome'] = 'skipped'
        rec['invalidation'] = 'missing_m15_atr'
        rec['entered'] = False
        return rec

    buf = 0.1 * atr_m15
    stop = post_extreme - buf if long_flip else post_extreme + buf
    rec['outcome'] = 'entered'
    rec['entry_ts'] = c['timestamp']
    rec['entry_price'] = c['close']
    rec['post_extreme'] = post_extreme
    rec['pullback_stop'] = stop   # simulate_trade stop_mode 'pullback'
    rec['breaker_stop'] = stop    # same stop under both keys (only one exists)
    rec['max_hold'] = cfg.max_hold_bars
    rec['reclaim_bars'] = rec['entry_idx'] - first_k + 1

    # Delta-turn selection flag (--select delta): does the delta picture turn
    # into the flip direction at the reclaim? True if the signed delta_pct
    # sum over the LAST THIRD of the break->reclaim window points in flip
    # direction OR delta_vs_ma on the reclaim candle does.
    window = m15[first_k:rec['entry_idx'] + 1]
    third = max(1, len(window) // 3)
    tail_sum = sum((w.get('delta_pct') or 0.0) for w in window[-third:])
    dvm = c.get('delta_vs_ma') or 0.0
    if long_flip:
        rec['delta_turn'] = 1 if (tail_sum > 0 or dvm > 0) else 0
    else:
        rec['delta_turn'] = 1 if (tail_sum < 0 or dvm < 0) else 0

    # Funding selection flag (--select funding): crowding at break time.
    # Point-in-time join: LAST funding print at or before the break candle's
    # open — never a print from inside or after the break candle.
    if funding is not None:
        rate = _funding_at(funding, b['timestamp'])
        rec['funding_rate'] = rate
        if rate is None:
            rec['funding_ok'] = 0
        elif long_flip:
            rec['funding_ok'] = 1 if rate < 0 else 0  # shorts crowded
        else:
            rec['funding_ok'] = 1 if rate > 0 else 0  # longs crowded
    return rec


def run_flips(h1, m15, cfg, funding=None):
    """Every H1 break becomes a flip candidate in the opposite direction."""
    m15_ts = [c['timestamp'] for c in m15]
    setups = []
    for i, b in enumerate(h1):
        if (b.get('break_depth') or 0) <= 0:
            continue
        if b.get('bos_bear') == 1:
            setups.append(track_flip(h1, i, 'long', m15, m15_ts, cfg, funding))
        if b.get('bos_bull') == 1:
            setups.append(track_flip(h1, i, 'short', m15, m15_ts, cfg, funding))
    return setups


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def _select_entries(entered_all, select_mode):
    """The one selection pipeline — shared by diagnose AND validate so OOS
    tests exactly the strategy that was diagnosed."""
    if select_mode == 'delta':
        return [s for s in entered_all if s.get('delta_turn') == 1]
    if select_mode == 'funding':
        return [s for s in entered_all if s.get('funding_ok') == 1]
    return entered_all


def _trades(m15, entered, cfg, target_fn):
    trades = []
    for s in entered:
        tgt = target_fn(s)
        if tgt[0] == 'price' and tgt[1] is None:
            continue
        t = simulate_trade(_sim_candles(s, m15), s, 'pullback', tgt, cfg)
        if t:
            trades.append(t)
    return trades


def run_diagnose(h1, m15, cfg, setups):
    split = int(len(h1) * cfg.train_fraction)
    train = [s for s in setups if s['breaker_index'] < split]
    entered_all = [s for s in train if s['entered']]
    missed = [s for s in train if s['outcome'] == 'missed']
    tracked = [s for s in train if s['outcome'] in ('entered', 'missed')]

    # Entry selection (--select): the one allowed selection lever.
    #   delta   — delta picture turns at the reclaim (rec['delta_turn'])
    #   funding — crowding: long flip only at NEGATIVE funding (shorts
    #             crowded -> squeeze carries the reclaim), short mirrored
    select_mode = getattr(cfg, 'select', 'none')
    selected = _select_entries(entered_all, select_mode)
    entered = selected if select_mode in ('delta', 'funding') else entered_all

    result = {
        'mode': 'diagnose',
        'strategy': 'flip_v1',
        'select': select_mode,
        'train_fraction': cfg.train_fraction,
        'h1_candles_total': len(h1),
        'train_h1_candles': split,
        'setups_train': len(train),
        'outcomes': _count_outcomes(train),
        'entered': len(entered),
        # Central hypothesis check: how many breakouts FAIL (get reclaimed)?
        'reclaim_quote': (round(len(entered) / len(tracked), 4)
                          if tracked else None),
        'tracked_total': len(tracked),
    }

    # Reclaim speed: M15 bars from break close to reclaim
    result['reclaim_bars'] = _percentiles([s.get('reclaim_bars') for s in entered])

    # Target comparison (single stop: post-break extreme -/+ 0.1xATR M15)
    result['targets'] = {}
    for name, tgt in {'1.5R': ('r', 1.5), '2R': ('r', 2.0), '3R': ('r', 3.0)}.items():
        trades = _trades(m15, entered, cfg, lambda s, t=tgt: t)
        result['targets'][name] = trade_stats(trades)
    trades = _trades(m15, entered, cfg, lambda s: ('price', s.get('swing_target')))
    result['targets']['swing_extreme'] = trade_stats(trades)

    # Baseline = 2R
    result['baseline_trades'] = _trades(m15, entered, cfg, lambda s: ('r', 2.0))
    base = result['baseline_trades']
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

    # Selection: does the filter lift the family above the cost line?
    # Reported with AND without filter, per target.
    result['selection'] = {
        'mode': select_mode,
        'selection_quote': (round(len(selected) / len(entered_all), 4)
                            if entered_all and select_mode != 'none' else None),
        'n_all': len(entered_all),
        'n_selected': len(selected),
        'active': select_mode in ('delta', 'funding'),
        'without_filter': {},
        'with_filter': {},
    }
    _tgt_fns = {'1.5R': lambda s: ('r', 1.5), '2R': lambda s: ('r', 2.0),
                '3R': lambda s: ('r', 3.0),
                'swing_extreme': lambda s: ('price', s.get('swing_target'))}
    for name, fn in _tgt_fns.items():
        result['selection']['without_filter'][name] = trade_stats(
            _trades(m15, entered_all, cfg, fn))
        result['selection']['with_filter'][name] = trade_stats(
            _trades(m15, selected, cfg, fn))

    # Cost sensitivity: setups/entries are cost-independent, re-simulate only.
    result['cost_sensitivity'] = {}
    for fee, slip in ((2.0, 5.0), (5.0, 5.0), (10.0, 10.0)):
        label = f'{fee:g}+{slip:g}'
        cfg2 = replace(cfg, fee_bps=fee, slippage_bps=slip)
        trades2 = _trades(m15, entered, cfg2, lambda s: ('r', 2.0))
        result['cost_sensitivity'][label] = {
            'baseline': trade_stats(trades2),
            'loss_breakdown': _loss_breakdown(trades2, cfg2),
        }
    return result


def run_validate(h1, m15, cfg, setups):
    """ONE config (2R baseline + ACTIVE selection), ONE OOS run."""
    split = int(len(h1) * cfg.train_fraction)
    oos = [s for s in setups if s['breaker_index'] >= split]
    select_mode = getattr(cfg, 'select', 'none')
    # Same selection pipeline as diagnose — otherwise OOS would test a
    # different strategy than the one that was diagnosed.
    entered = _select_entries([s for s in oos if s['entered']], select_mode)
    trades = _trades(m15, entered, cfg, lambda s: ('r', 2.0))
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
        'strategy': 'flip_v1',
        'select': select_mode,
        'n_selected_oos': len(entered),
        'config': {'stop': 'post_break_extreme-/+0.1ATR(M15)', 'target': '2R'},
        'oos_h1_candles': len(h1) - split,
        'setups_oos': len(oos),
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
    lines = ['# Fakeout-Flip Backtest — STRATEGY_FLIP_V1', '']
    lines.append(f"Mode: **{result['mode']}** | "
                 f"TFs: {cfg.setup_tf}/{cfg.exec_tf} | "
                 f"Costs: {cfg.fee_bps}+{cfg.slippage_bps} bps/side | "
                 f"Train-Fraction: {cfg.train_fraction}")
    lines.append('')
    if result['mode'] == 'diagnose':
        lines.append(f"H1 candles: {result['h1_candles_total']} "
                     f"(train: {result['train_h1_candles']})")
        lines.append(f"Setups (train): {result['setups_train']}, "
                     f"entered: {result['entered']}")
        lines.append(f"**Reclaim quote: {result['reclaim_quote']}** "
                     f"(of {result['tracked_total']} tracked breaks)")
        lines.append(f"Outcomes: `{json.dumps(result['outcomes'])}`")
        lines.append('')
        lines.append(f"## Reclaim speed (M15 bars)\n`{json.dumps(result['reclaim_bars'])}`")
        lines.append('')
        lines.append('## Target comparison')
        for name, st in result['targets'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append(f"## Loss anatomy (baseline 2R)\n`{json.dumps(result['loss_breakdown'])}`")
        lines.append('')
        lines.append('## By direction (baseline 2R)')
        for name, st in result['by_direction'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append('## By half-year (baseline 2R)')
        for name, st in result['by_halfyear'].items():
            lines.append(f"- {name}: `{json.dumps(st)}`")
        lines.append('')
        lines.append('## Selection filter (with/without, per target)')
        lines.append(f"`{json.dumps(result['selection'])}`")
        lines.append('')
        lines.append('## Cost sensitivity (baseline 2R)')
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


def main(argv=None):
    p = argparse.ArgumentParser(description='Fakeout-Flip Backtest (STRATEGY_FLIP_V1)')
    p.add_argument('--db', required=True, help='SQLite DB path (opened read-only)')
    p.add_argument('--mode', choices=['diagnose', 'validate'], required=True)
    p.add_argument('--setup-tf', default='1h', choices=sorted(TF_MS),
                   help='timeframe of break detection (default: 1h)')
    p.add_argument('--exec-tf', default='15m', choices=sorted(TF_MS),
                   help='timeframe of reclaim scan / trade sim (default: 15m)')
    p.add_argument('--validity', type=int, default=None,
                   help='setup validity in setup-tf candles '
                        '(default: 48 for 1h, 24 otherwise)')
    p.add_argument('--select', choices=['none', 'delta', 'funding'], default='none',
                   help='entry selection filter: delta = delta turns at reclaim; '
                        'funding = crowding (long flip only at negative funding)')
    p.add_argument('--funding-db', help='path to funding.db (read-only, '
                                        'required for --select funding)')
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
                 validity_h1=(args.validity if args.validity is not None
                              else (48 if args.setup_tf == '1h' else 24)))
    cfg.select = args.select

    # Optional funding series for --select funding (read-only, separate DB).
    funding = None
    if args.select == 'funding' or args.funding_db:
        if not args.funding_db:
            print('ERROR: --select funding needs --funding-db', file=sys.stderr)
            return 1
        try:
            fconn = sqlite3.connect(f'file:{args.funding_db}?mode=ro', uri=True)
            funding = fconn.execute(
                'SELECT ts_ms, rate FROM funding ORDER BY ts_ms').fetchall()
            fconn.close()
        except sqlite3.Error as e:
            raise SystemExit(
                f'funding.db nicht lesbar ({args.funding_db}): {e} — erst '
                'funding_loader laufen lassen oder --select weglassen.')
        if not funding:
            raise SystemExit(
                f'funding.db fehlt/leer ({args.funding_db}): 0 Prints — erst '
                'funding_loader laufen lassen oder --select weglassen. '
                'Ohne Funding-Daten waehlt der Filter nichts aus.')
        print(f'Loaded {len(funding):,} funding prints')

    since_ms = _date_to_ms(args.since) if args.since else 0
    until_ms = _date_to_ms(args.until) if args.until else 2**62

    # READ-ONLY: the productive DB must never be opened for writing.
    conn = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        h1 = load_candles(conn, args.setup_tf, since_ms, until_ms)
        m15 = load_candles(conn, args.exec_tf, since_ms, until_ms)
        run_meta = collect_run_meta(
            'backtest_flip.py', sys.argv, cfg, args.db, conn,
            [f'candles_{args.setup_tf}', f'candles_{args.exec_tf}'])
    finally:
        conn.close()

    print(f'Loaded {len(h1):,} {args.setup_tf} + {len(m15):,} {args.exec_tf} candles')
    if not h1 or not m15:
        print('ERROR: no candle data in range', file=sys.stderr)
        return 1

    setups = run_flips(h1, m15, cfg, funding)
    print(f'Flip setups tracked: {len(setups):,} '
          f'(reclaimed: {sum(1 for s in setups if s["entered"]):,})')

    if args.mode == 'diagnose':
        result = run_diagnose(h1, m15, cfg, setups)
    else:
        result = run_validate(h1, m15, cfg, setups)

    result['config'] = asdict(cfg)
    result['run_meta'] = run_meta
    result['range'] = {'since': args.since, 'until': args.until}

    if args.json_out:
        with open(args.json_out, 'w') as f:
            json.dump(result, f, indent=1)
        print(f'JSON -> {args.json_out}')
        from evidence import build_evidence
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        split = int(len(h1) * cfg.train_fraction)
        cutoff = h1[split]['timestamp'] if split < len(h1) else None
        loader_files = ([os.path.join(repo_dir, 'funding_loader.py')]
                        if args.funding_db else None)
        ev_path = build_evidence(
            script='backtest_flip.py', mode=args.mode, argv=sys.argv,
            cfg=cfg, db_path=args.db, result_path=args.json_out,
            tables=[f'candles_{args.setup_tf}', f'candles_{args.exec_tf}'],
            adapter_name='binance-futures',
            adapter_files=[os.path.join(repo_dir, 'hyperliquid_api.py'),
                           os.path.join(repo_dir, 'history_loader.py')],
            loader_files=loader_files,
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
