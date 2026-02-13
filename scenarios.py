#!/usr/bin/env python3
"""
Edgerunner Scenarios — Filter, label, and compare candles.

Scenarios are named filter-sets with labeled candles and outcome tracking.
Supports multi-TF via timeframe parameter.
"""
import json
import sqlite3
import statistics
from typing import Optional

from db import (DEFAULT_DB_PATH, FEATURE_COLUMNS, NUMERIC_FEATURES,
                _connect, _table_name, filter_candles)


class ScenarioManager:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path

    # =========================================================================
    # SCENARIO CRUD
    # =========================================================================

    def create(self, name: str, description: str = '') -> int:
        conn = _connect(self.db_path)
        cursor = conn.execute(
            'INSERT INTO scenarios (name, description) VALUES (?, ?)',
            (name, description)
        )
        conn.commit()
        sid = cursor.lastrowid
        conn.close()
        return sid

    def delete(self, name: str):
        conn = _connect(self.db_path)
        row = conn.execute('SELECT id FROM scenarios WHERE name = ?', (name,)).fetchone()
        if row:
            conn.execute('DELETE FROM scenario_candles WHERE scenario_id = ?', (row['id'],))
            conn.execute('DELETE FROM scenarios WHERE id = ?', (row['id'],))
            conn.commit()
        conn.close()

    def list_all(self) -> list:
        conn = _connect(self.db_path)
        rows = conn.execute('''
            SELECT s.*, COUNT(sc.candle_id) as candle_count
            FROM scenarios s
            LEFT JOIN scenario_candles sc ON s.id = sc.scenario_id
            GROUP BY s.id
            ORDER BY s.created_at DESC
        ''').fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # =========================================================================
    # CANDLE FILTERING
    # =========================================================================

    def filter(self, tf='1m', bos_bull=None, bos_bear=None, is_seeker_kill=None,
               bull_div=None, bear_div=None, min_body_ratio=None,
               min_vol_vs_ma=None, rsi_range=None, custom_sql=None,
               limit=1000) -> list:
        """Filter candles by feature criteria from candles_<tf>."""
        table = _table_name(tf)
        conditions = []
        params = []

        if bos_bull is not None:
            conditions.append('bos_bull = ?')
            params.append(1 if bos_bull else 0)
        if bos_bear is not None:
            conditions.append('bos_bear = ?')
            params.append(1 if bos_bear else 0)
        if is_seeker_kill is not None:
            conditions.append('is_seeker_kill = ?')
            params.append(1 if is_seeker_kill else 0)
        if bull_div is not None:
            conditions.append('bull_div = ?')
            params.append(1 if bull_div else 0)
        if bear_div is not None:
            conditions.append('bear_div = ?')
            params.append(1 if bear_div else 0)
        if min_body_ratio is not None:
            conditions.append('body_ratio >= ?')
            params.append(min_body_ratio)
        if min_vol_vs_ma is not None:
            conditions.append('vol_vs_ma >= ?')
            params.append(min_vol_vs_ma)
        if rsi_range is not None:
            lo, hi = rsi_range
            if lo is not None:
                conditions.append('rsi14 >= ?')
                params.append(lo)
            if hi is not None:
                conditions.append('rsi14 <= ?')
                params.append(hi)
        if custom_sql:
            conditions.append(f'({custom_sql})')

        where = ' AND '.join(conditions) if conditions else '1=1'
        conn = _connect(self.db_path)
        rows = conn.execute(
            f'SELECT * FROM {table} WHERE {where} ORDER BY timestamp LIMIT ?',
            params + [limit]
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # =========================================================================
    # ADD CANDLES TO SCENARIO
    # =========================================================================

    def add_candle(self, scenario_name: str, candle_ts: int, label: str,
                   tf: str = '1m', notes: str = None):
        """Add a single candle to a scenario by timestamp."""
        table = _table_name(tf)
        conn = _connect(self.db_path)
        scenario = conn.execute('SELECT id FROM scenarios WHERE name = ?',
                                (scenario_name,)).fetchone()
        if not scenario:
            conn.close()
            raise ValueError(f'Scenario not found: {scenario_name}')

        candle = conn.execute(f'SELECT id FROM {table} WHERE timestamp = ?',
                              (candle_ts,)).fetchone()
        if not candle:
            conn.close()
            raise ValueError(f'Candle not found: ts={candle_ts} in {table}')

        conn.execute(
            'INSERT OR REPLACE INTO scenario_candles (scenario_id, candle_id, timeframe, label, notes) VALUES (?, ?, ?, ?, ?)',
            (scenario['id'], candle['id'], tf, label, notes)
        )
        conn.commit()
        conn.close()

    def add_filtered(self, scenario_name: str, label: str, tf: str = '1m', **filter_kwargs):
        """Add all candles matching filter criteria to a scenario."""
        candles = self.filter(tf=tf, **filter_kwargs)
        conn = _connect(self.db_path)
        scenario = conn.execute('SELECT id FROM scenarios WHERE name = ?',
                                (scenario_name,)).fetchone()
        if not scenario:
            conn.close()
            raise ValueError(f'Scenario not found: {scenario_name}')

        for c in candles:
            conn.execute(
                'INSERT OR REPLACE INTO scenario_candles (scenario_id, candle_id, timeframe, label) VALUES (?, ?, ?, ?)',
                (scenario['id'], c['id'], tf, label)
            )
        conn.commit()
        conn.close()
        return len(candles)

    # =========================================================================
    # OUTCOMES
    # =========================================================================

    def compute_outcomes(self, candle_id: int, tf: str = '1m') -> Optional[dict]:
        """Compute what happened after a specific candle."""
        table = _table_name(tf)
        conn = _connect(self.db_path)
        candle = conn.execute(f'SELECT * FROM {table} WHERE id = ?', (candle_id,)).fetchone()
        if not candle:
            conn.close()
            return None

        ts = candle['timestamp']
        price = candle['close']

        offsets = {
            '1m': 60_000,
            '5m': 300_000,
            '15m': 900_000,
            '1h': 3_600_000,
        }
        outcome = {'candle_id': candle_id, 'timeframe': tf}

        future = conn.execute(
            f'SELECT close, high, low FROM {table} WHERE timestamp > ? ORDER BY timestamp LIMIT 60',
            (ts,)
        ).fetchall()

        for label, offset_ms in offsets.items():
            target_ts = ts + offset_ms
            future_candle = conn.execute(
                f'SELECT close FROM {table} WHERE timestamp >= ? ORDER BY timestamp LIMIT 1',
                (target_ts,)
            ).fetchone()
            if future_candle:
                fp = future_candle['close']
                outcome[f'price_{label}'] = fp
                outcome[f'pnl_{label}'] = (fp - price) / price * 100
            else:
                outcome[f'price_{label}'] = None
                outcome[f'pnl_{label}'] = None

        if future:
            max_high = max(r['high'] for r in future)
            min_low = min(r['low'] for r in future)
            outcome['max_favorable'] = (max_high - price) / price * 100
            outcome['max_adverse'] = (price - min_low) / price * 100
        else:
            outcome['max_favorable'] = None
            outcome['max_adverse'] = None

        conn.execute('''
            INSERT OR REPLACE INTO outcomes
            (candle_id, timeframe, price_1m, pnl_1m, price_5m, pnl_5m,
             price_15m, pnl_15m, price_1h, pnl_1h,
             max_favorable, max_adverse)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (candle_id, tf,
              outcome.get('price_1m'), outcome.get('pnl_1m'),
              outcome.get('price_5m'), outcome.get('pnl_5m'),
              outcome.get('price_15m'), outcome.get('pnl_15m'),
              outcome.get('price_1h'), outcome.get('pnl_1h'),
              outcome.get('max_favorable'), outcome.get('max_adverse')))
        conn.commit()
        conn.close()
        return outcome

    def compute_outcomes_bulk(self, scenario_name: str, tf: str = '1m') -> int:
        """Compute outcomes for all candles in a scenario."""
        conn = _connect(self.db_path)
        scenario = conn.execute('SELECT id FROM scenarios WHERE name = ?',
                                (scenario_name,)).fetchone()
        if not scenario:
            conn.close()
            return 0

        candle_ids = conn.execute(
            'SELECT candle_id FROM scenario_candles WHERE scenario_id = ? AND timeframe = ?',
            (scenario['id'], tf)
        ).fetchall()
        conn.close()

        count = 0
        for row in candle_ids:
            result = self.compute_outcomes(row['candle_id'], tf=tf)
            if result:
                count += 1
        return count

    # =========================================================================
    # COMPARE / ANALYZE
    # =========================================================================

    def compare_scenario(self, scenario_name: str, tf: str = '1m') -> dict:
        """Compute statistics for all candles in a scenario."""
        table = _table_name(tf)
        conn = _connect(self.db_path)
        scenario = conn.execute('SELECT id FROM scenarios WHERE name = ?',
                                (scenario_name,)).fetchone()
        if not scenario:
            conn.close()
            return {}

        rows = conn.execute(f'''
            SELECT c.*, o.pnl_1m, o.pnl_5m, o.pnl_15m, o.pnl_1h,
                   o.max_favorable, o.max_adverse, sc.label
            FROM scenario_candles sc
            JOIN {table} c ON sc.candle_id = c.id
            LEFT JOIN outcomes o ON c.id = o.candle_id AND o.timeframe = ?
            WHERE sc.scenario_id = ? AND sc.timeframe = ?
        ''', (tf, scenario['id'], tf)).fetchall()
        conn.close()

        if not rows:
            return {'count': 0}

        rows = [dict(r) for r in rows]

        feature_avgs = {}
        for feat in NUMERIC_FEATURES:
            vals = [r[feat] for r in rows if r.get(feat) is not None]
            if vals:
                feature_avgs[feat] = {
                    'mean': statistics.mean(vals),
                    'std': statistics.stdev(vals) if len(vals) > 1 else 0.0,
                    'min': min(vals),
                    'max': max(vals),
                }

        outcome_stats = {}
        for otf in ['1m', '5m', '15m', '1h']:
            pnl_key = f'pnl_{otf}'
            pnls = [r[pnl_key] for r in rows if r.get(pnl_key) is not None]
            if pnls:
                wins = [p for p in pnls if p > 0]
                outcome_stats[otf] = {
                    'avg_pnl': statistics.mean(pnls),
                    'std_pnl': statistics.stdev(pnls) if len(pnls) > 1 else 0.0,
                    'win_rate': len(wins) / len(pnls) * 100,
                    'count': len(pnls),
                }

        fav = [r['max_favorable'] for r in rows if r.get('max_favorable') is not None]
        adv = [r['max_adverse'] for r in rows if r.get('max_adverse') is not None]

        labels = {}
        for r in rows:
            l = r.get('label', 'unknown')
            labels[l] = labels.get(l, 0) + 1

        return {
            'count': len(rows),
            'timeframe': tf,
            'labels': labels,
            'outcomes': outcome_stats,
            'avg_max_favorable': statistics.mean(fav) if fav else None,
            'avg_max_adverse': statistics.mean(adv) if adv else None,
            'feature_averages': feature_avgs,
        }


if __name__ == '__main__':
    sm = ScenarioManager()
    print('Scenarios:', sm.list_all())
