#!/usr/bin/env python3
"""
Edgerunner Database — SQLite Schema + CRUD for candle features.

Multi-TF: Separate tables per timeframe (candles_1m, candles_5m, ..., candles_1M).
One row per candle, all 89 features as columns.
"""
import sqlite3
import os
from typing import Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'edgerunner.db')

TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1M']

# Column definitions (shared across all TF tables)
CANDLE_COLUMNS_SQL = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Rohdaten (7)
    timestamp INTEGER UNIQUE NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, delta REAL,
    -- Kerzen-Anatomie (8)
    body_size REAL, upper_wick REAL, lower_wick REAL, total_range REAL,
    body_ratio REAL, wick_ratio REAL, body_position REAL, is_bullish INTEGER,
    -- Volume/Delta (3)
    delta_pct REAL, vol_vs_ma REAL, delta_vs_ma REAL,
    -- Swing Structure (7)
    is_swing_high INTEGER, is_swing_low INTEGER, bos_bull INTEGER, bos_bear INTEGER,
    choch INTEGER, dist_swing_high REAL, dist_swing_low REAL,
    -- Break Quality (9)
    bos_body INTEGER, bos_wick INTEGER, break_depth REAL, swing_age INTEGER,
    swing_age_norm REAL, breaks_highs INTEGER, breaks_lows INTEGER,
    max_age_broken INTEGER, min_age_broken INTEGER,
    -- Paarung Brecher vs Swing (13)
    sw_body_ratio REAL, sw_wick_ratio REAL, sw_delta_pct REAL, sw_vol_rel REAL,
    sw_bullish INTEGER, sw_body_pos REAL, sw_ohlc TEXT,
    vol_ratio_bsw REAL, delta_ratio_bsw REAL, body_ratio_bsw REAL,
    same_dir INTEGER, broken_was_seeker INTEGER, broken_was_seeker_div INTEGER,
    -- Kette (3)
    swing_had_break INTEGER, chain_depth INTEGER, prev_swing_features TEXT,
    -- Cluster (3)
    cluster_range REAL, cluster_range_atr REAL, cluster_spread INTEGER,
    -- MACD + Divergenzen (8)
    macd_line REAL, macd_peak INTEGER, macd_trough INTEGER,
    bull_div INTEGER, bear_div INTEGER, div_near_daily INTEGER,
    div_strength REAL, div_width INTEGER,
    -- Seeker (10)
    is_seeker_hs INTEGER, is_seeker_ls INTEGER, is_seeker_div INTEGER,
    seeker_div_nr INTEGER, dist_prev_seeker_div INTEGER,
    dist_prev_seeker_div_norm REAL, is_seeker_kill INTEGER,
    killed_seeker_divs INTEGER, candle_was_seeker INTEGER, candle_was_seeker_div INTEGER,
    -- Kontext/Trend (6)
    ema21_dist REAL, ema50_dist REAL, ema200_dist REAL,
    atr14 REAL, rsi14 REAL, vwap_dist REAL,
    -- Multi-TF (4)
    htf_trend INTEGER, htf_swing_high REAL, htf_swing_low REAL, htf_bos INTEGER,
    -- Whale Features (8)
    whale_sentiment REAL, whale_confidence REAL, bull_pressure REAL, bear_pressure REAL,
    whale_cluster INTEGER, whale_cluster_strength REAL, whale_cluster_dir INTEGER,
    elite_whale_active INTEGER
"""

# Scenarios + outcomes schema (with timeframe)
AUTH_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);
"""

EXTRA_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scenario_candles (
    scenario_id INTEGER REFERENCES scenarios(id),
    candle_id INTEGER,
    timeframe TEXT NOT NULL DEFAULT '1m',
    label TEXT NOT NULL CHECK(label IN ('long', 'short', 'neutral')),
    notes TEXT,
    PRIMARY KEY (scenario_id, candle_id, timeframe)
);

CREATE TABLE IF NOT EXISTS outcomes (
    candle_id INTEGER,
    timeframe TEXT NOT NULL DEFAULT '1m',
    price_1m REAL, pnl_1m REAL,
    price_5m REAL, pnl_5m REAL,
    price_15m REAL, pnl_15m REAL,
    price_1h REAL, pnl_1h REAL,
    max_favorable REAL,
    max_adverse REAL,
    PRIMARY KEY (candle_id, timeframe)
);

CREATE TABLE IF NOT EXISTS analyzer_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def _table_name(tf):
    """'1m' -> 'candles_1m', '1M' -> 'candles_1M'"""
    return f'candles_{tf}'


def _create_candle_table_sql(tf):
    """Generate CREATE TABLE + indexes for a specific timeframe."""
    table = _table_name(tf)
    return f"""
CREATE TABLE IF NOT EXISTS {table} (
{CANDLE_COLUMNS_SQL}
);
CREATE INDEX IF NOT EXISTS idx_{table}_ts ON {table}(timestamp);
CREATE INDEX IF NOT EXISTS idx_{table}_bos ON {table}(bos_bull, bos_bear);
CREATE INDEX IF NOT EXISTS idx_{table}_seeker ON {table}(is_seeker_hs, is_seeker_ls, is_seeker_kill);
CREATE INDEX IF NOT EXISTS idx_{table}_div ON {table}(bull_div, bear_div);
"""


# All feature column names in DB order (excluding id)
FEATURE_COLUMNS = [
    'timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta',
    'body_size', 'upper_wick', 'lower_wick', 'total_range',
    'body_ratio', 'wick_ratio', 'body_position', 'is_bullish',
    'delta_pct', 'vol_vs_ma', 'delta_vs_ma',
    'is_swing_high', 'is_swing_low', 'bos_bull', 'bos_bear',
    'choch', 'dist_swing_high', 'dist_swing_low',
    'bos_body', 'bos_wick', 'break_depth', 'swing_age',
    'swing_age_norm', 'breaks_highs', 'breaks_lows',
    'max_age_broken', 'min_age_broken',
    'sw_body_ratio', 'sw_wick_ratio', 'sw_delta_pct', 'sw_vol_rel',
    'sw_bullish', 'sw_body_pos', 'sw_ohlc',
    'vol_ratio_bsw', 'delta_ratio_bsw', 'body_ratio_bsw',
    'same_dir', 'broken_was_seeker', 'broken_was_seeker_div',
    'swing_had_break', 'chain_depth', 'prev_swing_features',
    'cluster_range', 'cluster_range_atr', 'cluster_spread',
    'macd_line', 'macd_peak', 'macd_trough',
    'bull_div', 'bear_div', 'div_near_daily',
    'div_strength', 'div_width',
    'is_seeker_hs', 'is_seeker_ls', 'is_seeker_div',
    'seeker_div_nr', 'dist_prev_seeker_div',
    'dist_prev_seeker_div_norm', 'is_seeker_kill',
    'killed_seeker_divs', 'candle_was_seeker', 'candle_was_seeker_div',
    'ema21_dist', 'ema50_dist', 'ema200_dist',
    'atr14', 'rsi14', 'vwap_dist',
    'htf_trend', 'htf_swing_high', 'htf_swing_low', 'htf_bos',
    'whale_sentiment', 'whale_confidence', 'bull_pressure', 'bear_pressure',
    'whale_cluster', 'whale_cluster_strength', 'whale_cluster_dir',
    'elite_whale_active',
]

# Numeric-only features for ML (no timestamp, no TEXT columns)
NUMERIC_FEATURES = [c for c in FEATURE_COLUMNS
                    if c not in ('timestamp', 'sw_ohlc', 'prev_swing_features')]

assert len(FEATURE_COLUMNS) == 89, f'Expected 89 columns, got {len(FEATURE_COLUMNS)}'


def _connect(path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def _migrate_old_candles_table(conn):
    """Migrate old 'candles' table to 'candles_1m' if it exists."""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='candles'"
    ).fetchall()]
    if 'candles' in tables:
        # Check if candles_1m already exists
        has_1m = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='candles_1m'"
        ).fetchone()
        if not has_1m:
            conn.execute('ALTER TABLE candles RENAME TO candles_1m')
            # Recreate indexes with new names
            conn.execute('CREATE INDEX IF NOT EXISTS idx_candles_1m_ts ON candles_1m(timestamp)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_candles_1m_bos ON candles_1m(bos_bull, bos_bear)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_candles_1m_seeker ON candles_1m(is_seeker_hs, is_seeker_ls, is_seeker_kill)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_candles_1m_div ON candles_1m(bull_div, bear_div)')
            conn.commit()
            print('  [DB] Migrated candles -> candles_1m')
        else:
            # Both exist — drop old candles table (data already in candles_1m)
            conn.execute('DROP TABLE IF EXISTS candles')
            conn.commit()

    # Migrate scenario_candles: add timeframe column if missing
    try:
        conn.execute("SELECT timeframe FROM scenario_candles LIMIT 1")
    except sqlite3.OperationalError:
        try:
            conn.execute("ALTER TABLE scenario_candles ADD COLUMN timeframe TEXT NOT NULL DEFAULT '1m'")
            conn.commit()
            print('  [DB] Added timeframe to scenario_candles')
        except sqlite3.OperationalError:
            pass

    # Migrate outcomes: add timeframe column if missing
    try:
        conn.execute("SELECT timeframe FROM outcomes LIMIT 1")
    except sqlite3.OperationalError:
        try:
            conn.execute("ALTER TABLE outcomes ADD COLUMN timeframe TEXT NOT NULL DEFAULT '1m'")
            conn.commit()
            print('  [DB] Added timeframe to outcomes')
        except sqlite3.OperationalError:
            pass


def init_db(path: str = DEFAULT_DB_PATH) -> str:
    """Create schema for all TF tables + scenarios/outcomes. Returns path."""
    conn = _connect(path)

    # Migrate old schema first
    _migrate_old_candles_table(conn)

    # Create all TF tables
    for tf in TIMEFRAMES:
        conn.executescript(_create_candle_table_sql(tf))

    # Create auth tables
    conn.executescript(AUTH_TABLES_SQL)

    # Create extra tables (scenarios, outcomes)
    conn.executescript(EXTRA_TABLES_SQL)

    conn.close()
    return path


def insert_candles(candles: list, tf: str = '1m', path: str = DEFAULT_DB_PATH):
    """Batch-insert candles with UPSERT into candles_<tf>."""
    if not candles:
        return 0
    table = _table_name(tf)
    cols = FEATURE_COLUMNS
    placeholders = ','.join(['?'] * len(cols))
    col_str = ','.join(cols)
    update_str = ','.join(f'{c}=excluded.{c}' for c in cols if c != 'timestamp')

    sql = f"""INSERT INTO {table} ({col_str}) VALUES ({placeholders})
              ON CONFLICT(timestamp) DO UPDATE SET {update_str}"""

    conn = _connect(path)
    inserted = 0
    try:
        batch = []
        for c in candles:
            row = tuple(c.get(col) for col in cols)
            batch.append(row)
        conn.executemany(sql, batch)
        inserted = conn.total_changes
        conn.commit()
    finally:
        conn.close()
    return inserted


def get_candles(start_ts: int, end_ts: int, tf: str = '1m',
                path: str = DEFAULT_DB_PATH) -> list:
    """Get candles in timestamp range from candles_<tf>."""
    table = _table_name(tf)
    conn = _connect(path)
    rows = conn.execute(
        f'SELECT * FROM {table} WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp',
        (start_ts, end_ts)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_candle_by_ts(ts: int, tf: str = '1m',
                     path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    """Get single candle by timestamp from candles_<tf>."""
    table = _table_name(tf)
    conn = _connect(path)
    row = conn.execute(f'SELECT * FROM {table} WHERE timestamp = ?', (ts,)).fetchone()
    conn.close()
    return dict(row) if row else None


def filter_candles(filters: dict, tf: str = '1m',
                   path: str = DEFAULT_DB_PATH) -> list:
    """Dynamic WHERE clause from filters dict on candles_<tf>."""
    table = _table_name(tf)
    conditions = []
    params = []
    for col, val in filters.items():
        if col not in FEATURE_COLUMNS:
            continue
        if isinstance(val, tuple) and len(val) == 2:
            lo, hi = val
            if lo is not None:
                conditions.append(f'{col} >= ?')
                params.append(lo)
            if hi is not None:
                conditions.append(f'{col} <= ?')
                params.append(hi)
        elif isinstance(val, bool):
            conditions.append(f'{col} = ?')
            params.append(1 if val else 0)
        else:
            conditions.append(f'{col} = ?')
            params.append(val)

    where = ' AND '.join(conditions) if conditions else '1=1'
    conn = _connect(path)
    rows = conn.execute(
        f'SELECT * FROM {table} WHERE {where} ORDER BY timestamp', params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_feature_matrix(start_ts: int, end_ts: int, tf: str = '1m',
                       path: str = DEFAULT_DB_PATH):
    """Return numpy array of all numeric features in time range."""
    import numpy as np
    table = _table_name(tf)
    cols_str = ','.join(NUMERIC_FEATURES)
    conn = _connect(path)
    rows = conn.execute(
        f'SELECT {cols_str} FROM {table} WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp',
        (start_ts, end_ts)
    ).fetchall()
    conn.close()
    if not rows:
        return np.empty((0, len(NUMERIC_FEATURES)))
    return np.array([[r[i] if r[i] is not None else 0.0
                      for i in range(len(NUMERIC_FEATURES))] for r in rows])


def count_candles(tf: str = None, path: str = DEFAULT_DB_PATH) -> int:
    """Count candles. If tf=None, return total across all TFs."""
    conn = _connect(path)
    if tf is not None:
        table = _table_name(tf)
        n = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    else:
        n = 0
        for t in TIMEFRAMES:
            table = _table_name(t)
            try:
                n += conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            except sqlite3.OperationalError:
                pass
    conn.close()
    return n


def get_ts_range(tf: str = '1m', path: str = DEFAULT_DB_PATH) -> Optional[tuple]:
    """Return (min_ts, max_ts) or None if empty."""
    table = _table_name(tf)
    conn = _connect(path)
    row = conn.execute(f'SELECT MIN(timestamp), MAX(timestamp) FROM {table}').fetchone()
    conn.close()
    if row and row[0] is not None:
        return (row[0], row[1])
    return None


def get_all_tf_stats(path: str = DEFAULT_DB_PATH) -> list:
    """Return stats for all timeframes: [{tf, count, min_ts, max_ts}]."""
    conn = _connect(path)
    stats = []
    for tf in TIMEFRAMES:
        table = _table_name(tf)
        try:
            row = conn.execute(
                f'SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {table}'
            ).fetchone()
            stats.append({
                'tf': tf,
                'count': row[0],
                'min_ts': row[1],
                'max_ts': row[2],
            })
        except sqlite3.OperationalError:
            stats.append({'tf': tf, 'count': 0, 'min_ts': None, 'max_ts': None})
    conn.close()
    return stats


# ============================================================================
# AUTH HELPERS
# ============================================================================

import hashlib
import secrets
from datetime import datetime, timedelta


def _hash_password(password: str) -> str:
    """Hash password with SHA-256 + salt. Simple but sufficient for local use."""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f'{salt}${h}'


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash."""
    try:
        salt, h = stored_hash.split('$', 1)
        return hashlib.sha256((salt + password).encode()).hexdigest() == h
    except (ValueError, AttributeError):
        return False


def create_user(username: str, password: str, path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    """Create a new user. Returns user dict or None if username taken."""
    conn = _connect(path)
    try:
        pw_hash = _hash_password(password)
        conn.execute(
            'INSERT INTO users (username, password_hash) VALUES (?, ?)',
            (username, pw_hash)
        )
        conn.commit()
        row = conn.execute('SELECT id, username, created_at FROM users WHERE username = ?', (username,)).fetchone()
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def authenticate_user(username: str, password: str, path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    """Authenticate user. Returns user dict or None."""
    conn = _connect(path)
    try:
        row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if not row:
            return None
        if not _verify_password(password, row['password_hash']):
            return None
        conn.execute('UPDATE users SET last_login = datetime("now") WHERE id = ?', (row['id'],))
        conn.commit()
        return {'id': row['id'], 'username': row['username']}
    finally:
        conn.close()


def create_session(user_id: int, days: int = 7, path: str = DEFAULT_DB_PATH) -> str:
    """Create a session token. Returns token string."""
    token = secrets.token_hex(32)
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    conn = _connect(path)
    try:
        conn.execute(
            'INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)',
            (token, user_id, expires)
        )
        conn.commit()
    finally:
        conn.close()
    return token


def validate_session(token: str, path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    """Validate session token. Returns user dict or None."""
    if not token:
        return None
    conn = _connect(path)
    try:
        row = conn.execute(
            '''SELECT s.user_id, u.username FROM sessions s
               JOIN users u ON s.user_id = u.id
               WHERE s.token = ? AND s.expires_at > datetime("now")''',
            (token,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_session(token: str, path: str = DEFAULT_DB_PATH):
    """Delete a session (logout)."""
    conn = _connect(path)
    try:
        conn.execute('DELETE FROM sessions WHERE token = ?', (token,))
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# SETTINGS HELPERS
# ============================================================================

import json as _json


def get_settings(path: str = DEFAULT_DB_PATH) -> dict:
    """Load all analyzer settings as a flat dict."""
    conn = _connect(path)
    rows = conn.execute('SELECT key, value FROM analyzer_settings').fetchall()
    conn.close()
    result = {}
    for r in rows:
        try:
            result[r['key']] = _json.loads(r['value'])
        except (_json.JSONDecodeError, TypeError):
            result[r['key']] = r['value']
    return result


def save_settings(settings: dict, path: str = DEFAULT_DB_PATH):
    """Upsert settings dict into analyzer_settings table."""
    conn = _connect(path)
    for key, value in settings.items():
        val_str = _json.dumps(value) if not isinstance(value, str) else value
        conn.execute(
            """INSERT INTO analyzer_settings (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, val_str)
        )
    conn.commit()
    conn.close()


# ============================================================================
# PAGINATION HELPERS
# ============================================================================

def get_candles_paginated(tf: str = '1m', page: int = 1, limit: int = 50,
                          order: str = 'desc', path: str = DEFAULT_DB_PATH) -> dict:
    """Paginated candle query. Returns {candles, total, page, pages}."""
    table = _table_name(tf)
    order_dir = 'DESC' if order.lower() == 'desc' else 'ASC'
    conn = _connect(path)
    total = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    pages = max(1, (total + limit - 1) // limit)
    page = max(1, min(page, pages))
    offset = (page - 1) * limit
    rows = conn.execute(
        f'SELECT * FROM {table} ORDER BY timestamp {order_dir} LIMIT ? OFFSET ?',
        (limit, offset)
    ).fetchall()
    conn.close()
    return {
        'candles': [dict(r) for r in rows],
        'total': total,
        'page': page,
        'pages': pages,
    }


def get_candle_neighbors(ts: int, tf: str = '1m', count: int = 5,
                          path: str = DEFAULT_DB_PATH) -> dict:
    """Get a candle and its neighbors. Returns {center, before[], after[]}."""
    table = _table_name(tf)
    conn = _connect(path)
    center = conn.execute(
        f'SELECT * FROM {table} WHERE timestamp = ?', (ts,)
    ).fetchone()
    if not center:
        conn.close()
        return {'center': None, 'before': [], 'after': []}
    before = conn.execute(
        f'SELECT * FROM {table} WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?',
        (ts, count)
    ).fetchall()
    after = conn.execute(
        f'SELECT * FROM {table} WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?',
        (ts, count)
    ).fetchall()
    conn.close()
    return {
        'center': dict(center),
        'before': [dict(r) for r in reversed(before)],
        'after': [dict(r) for r in after],
    }


if __name__ == '__main__':
    p = init_db()
    print(f'DB initialized: {p}')
    print(f'Feature columns: {len(FEATURE_COLUMNS)}')
    print(f'Numeric features: {len(NUMERIC_FEATURES)}')
    print()
    for s in get_all_tf_stats(p):
        print(f"  {s['tf']:>3s}: {s['count']:>10,} candles")
