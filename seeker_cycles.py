#!/usr/bin/env python3
"""
Seeker Cycle Engine v1 — persist Seeker origins and their core lifecycle events.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from candle_analyzer import _check_seeker_div, _check_seeker_kill, _seeker_wick_zone

SEEKER_CYCLE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS seeker_cycles (
    cycle_id TEXT PRIMARY KEY,
    timeframe TEXT NOT NULL,
    cycle_type TEXT NOT NULL CHECK(cycle_type IN ('HS', 'LS')),
    status TEXT NOT NULL CHECK(status IN ('open', 'killed')),
    origin_ts INTEGER NOT NULL,
    origin_index INTEGER NOT NULL,
    origin_open REAL,
    origin_high REAL,
    origin_low REAL,
    origin_close REAL,
    zone_top REAL,
    zone_bottom REAL,
    zone_size REAL,
    zone_pct_of_range REAL,
    zone_vs_body REAL,
    wick_dominance REAL,
    origin_body_ratio REAL,
    origin_wick_ratio REAL,
    origin_body_position REAL,
    div_count_total INTEGER DEFAULT 0,
    last_div_ts INTEGER,
    last_kill_ts INTEGER,
    age_bars INTEGER DEFAULT 0,
    age_ms INTEGER DEFAULT 0,
    time_to_first_div_bars INTEGER,
    time_to_first_div_ms INTEGER,
    time_to_kill_bars INTEGER,
    time_to_kill_ms INTEGER,
    last_event_ts INTEGER,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_seeker_cycles_tf_status ON seeker_cycles(timeframe, status, cycle_type);
CREATE INDEX IF NOT EXISTS idx_seeker_cycles_origin ON seeker_cycles(timeframe, origin_ts);

CREATE TABLE IF NOT EXISTS seeker_cycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL REFERENCES seeker_cycles(cycle_id) ON DELETE CASCADE,
    timeframe TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('origin', 'div', 'kill')),
    ts INTEGER NOT NULL,
    candle_ts INTEGER NOT NULL,
    distance_from_origin_bars INTEGER,
    distance_from_origin_ms INTEGER,
    meta_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(cycle_id, event_type, candle_ts)
);
CREATE INDEX IF NOT EXISTS idx_seeker_cycle_events_tf_ts ON seeker_cycle_events(timeframe, candle_ts);
CREATE INDEX IF NOT EXISTS idx_seeker_cycle_events_cycle ON seeker_cycle_events(cycle_id, candle_ts);
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SEEKER_CYCLE_TABLES_SQL)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _cycle_id(tf: str, cycle_type: str, origin_ts: int) -> str:
    return f"{tf}:{cycle_type}:{origin_ts}"


def _to_num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _origin_zone_metrics(candle: dict, cycle_type: str) -> dict:
    zone_top, zone_bottom = _seeker_wick_zone(candle, cycle_type)
    zone_size = abs(zone_top - zone_bottom)
    total_range = max(1e-8, _to_num(candle.get("high")) - _to_num(candle.get("low")))
    body_size = max(1e-8, abs(_to_num(candle.get("close")) - _to_num(candle.get("open"))))
    upper_wick = _to_num(candle.get("upper_wick"))
    lower_wick = _to_num(candle.get("lower_wick"))
    dominant = upper_wick if cycle_type == "HS" else lower_wick
    opposite = lower_wick if cycle_type == "HS" else upper_wick
    return {
        "zone_top": zone_top,
        "zone_bottom": zone_bottom,
        "zone_size": zone_size,
        "zone_pct_of_range": zone_size / total_range,
        "zone_vs_body": zone_size / body_size,
        "wick_dominance": dominant / max(1e-8, opposite),
    }


def _origin_cycle_row(tf: str, candle: dict, cycle_type: str, index: int) -> dict:
    origin_ts = int(candle["timestamp"])
    metrics = _origin_zone_metrics(candle, cycle_type)
    return {
        "cycle_id": _cycle_id(tf, cycle_type, origin_ts),
        "timeframe": tf,
        "cycle_type": cycle_type,
        "status": "open",
        "origin_ts": origin_ts,
        "origin_index": index,
        "origin_open": _to_num(candle.get("open")),
        "origin_high": _to_num(candle.get("high")),
        "origin_low": _to_num(candle.get("low")),
        "origin_close": _to_num(candle.get("close")),
        "zone_top": metrics["zone_top"],
        "zone_bottom": metrics["zone_bottom"],
        "zone_size": metrics["zone_size"],
        "zone_pct_of_range": metrics["zone_pct_of_range"],
        "zone_vs_body": metrics["zone_vs_body"],
        "wick_dominance": metrics["wick_dominance"],
        "origin_body_ratio": _to_num(candle.get("body_ratio")),
        "origin_wick_ratio": _to_num(candle.get("wick_ratio")),
        "origin_body_position": _to_num(candle.get("body_position")),
        "div_count_total": 0,
        "last_div_ts": None,
        "last_kill_ts": None,
        "age_bars": 0,
        "age_ms": 0,
        "time_to_first_div_bars": None,
        "time_to_first_div_ms": None,
        "time_to_kill_bars": None,
        "time_to_kill_ms": None,
        "last_event_ts": origin_ts,
    }


def _cycle_origin_candle(cycle: dict) -> dict:
    return {
        "timestamp": cycle["origin_ts"],
        "open": cycle["origin_open"],
        "high": cycle["origin_high"],
        "low": cycle["origin_low"],
        "close": cycle["origin_close"],
    }


def _load_candles(conn: sqlite3.Connection, tf: str) -> list[dict]:
    rows = conn.execute(
        f"""SELECT timestamp, open, high, low, close, body_ratio, wick_ratio, body_position,
                   upper_wick, lower_wick, total_range,
                   is_seeker_hs, is_seeker_ls
            FROM candles_{tf}
            ORDER BY timestamp"""
    ).fetchall()
    return [dict(r) for r in rows]


def _insert_cycle(conn: sqlite3.Connection, cycle: dict) -> None:
    conn.execute(
        """
        INSERT INTO seeker_cycles (
            cycle_id, timeframe, cycle_type, status, origin_ts, origin_index,
            origin_open, origin_high, origin_low, origin_close,
            zone_top, zone_bottom, zone_size, zone_pct_of_range, zone_vs_body, wick_dominance,
            origin_body_ratio, origin_wick_ratio, origin_body_position,
            div_count_total, last_div_ts, last_kill_ts,
            age_bars, age_ms, time_to_first_div_bars, time_to_first_div_ms,
            time_to_kill_bars, time_to_kill_ms, last_event_ts, updated_at
        ) VALUES (
            :cycle_id, :timeframe, :cycle_type, :status, :origin_ts, :origin_index,
            :origin_open, :origin_high, :origin_low, :origin_close,
            :zone_top, :zone_bottom, :zone_size, :zone_pct_of_range, :zone_vs_body, :wick_dominance,
            :origin_body_ratio, :origin_wick_ratio, :origin_body_position,
            :div_count_total, :last_div_ts, :last_kill_ts,
            :age_bars, :age_ms, :time_to_first_div_bars, :time_to_first_div_ms,
            :time_to_kill_bars, :time_to_kill_ms, :last_event_ts, datetime('now')
        )
        ON CONFLICT(cycle_id) DO UPDATE SET
            status=excluded.status,
            div_count_total=excluded.div_count_total,
            last_div_ts=excluded.last_div_ts,
            last_kill_ts=excluded.last_kill_ts,
            age_bars=excluded.age_bars,
            age_ms=excluded.age_ms,
            time_to_first_div_bars=excluded.time_to_first_div_bars,
            time_to_first_div_ms=excluded.time_to_first_div_ms,
            time_to_kill_bars=excluded.time_to_kill_bars,
            time_to_kill_ms=excluded.time_to_kill_ms,
            last_event_ts=excluded.last_event_ts,
            updated_at=datetime('now')
        """,
        cycle,
    )


def _insert_event(conn: sqlite3.Connection, event: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO seeker_cycle_events (
            cycle_id, timeframe, event_type, ts, candle_ts,
            distance_from_origin_bars, distance_from_origin_ms, meta_json
        ) VALUES (
            :cycle_id, :timeframe, :event_type, :ts, :candle_ts,
            :distance_from_origin_bars, :distance_from_origin_ms, :meta_json
        )
        """,
        event,
    )


def _event_payload(cycle: dict, tf: str, event_type: str, candle: dict, current_index: int) -> dict:
    origin_ts = int(cycle["origin_ts"])
    return {
        "cycle_id": cycle["cycle_id"],
        "timeframe": tf,
        "event_type": event_type,
        "ts": int(candle["timestamp"]),
        "candle_ts": int(candle["timestamp"]),
        "distance_from_origin_bars": current_index - int(cycle["origin_index"]),
        "distance_from_origin_ms": int(candle["timestamp"]) - origin_ts,
        "meta_json": json.dumps({
            "price": _to_num(candle.get("close")),
            "open": _to_num(candle.get("open")),
            "high": _to_num(candle.get("high")),
            "low": _to_num(candle.get("low")),
            "close": _to_num(candle.get("close")),
        }),
    }


def _active_cycles_from_db(conn: sqlite3.Connection, tf: str) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM seeker_cycles
           WHERE timeframe = ? AND status = 'open'
           ORDER BY origin_ts""",
        (tf,),
    ).fetchall()
    return [dict(r) for r in rows]


def _latest_event_ts(conn: sqlite3.Connection, tf: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(candle_ts), 0) FROM seeker_cycle_events WHERE timeframe = ?",
        (tf,),
    ).fetchone()
    return int(row[0] or 0)


def rebuild_seeker_cycles(path: str, timeframes: Iterable[str]) -> None:
    conn = _connect(path)
    ensure_tables(conn)
    with conn:
        for tf in timeframes:
            conn.execute("DELETE FROM seeker_cycle_events WHERE timeframe = ?", (tf,))
            conn.execute("DELETE FROM seeker_cycles WHERE timeframe = ?", (tf,))
            candles = _load_candles(conn, tf)
            _replay_cycles(conn, tf, candles, start_from_existing=False)
    conn.close()


def sync_seeker_cycles_incremental(candles: list[dict], tf: str, path: str) -> None:
    if not candles:
        return
    conn = _connect(path)
    ensure_tables(conn)
    latest_known = _latest_event_ts(conn, tf)
    fresh = [
        dict(item)
        for item in sorted(candles, key=lambda entry: int(entry["timestamp"]))
        if int(item["timestamp"]) > latest_known
    ]
    if not fresh:
        conn.close()
        return
    absolute_offset = conn.execute(
        f"SELECT COUNT(*) FROM candles_{tf} WHERE timestamp < ?",
        (int(fresh[0]["timestamp"]),),
    ).fetchone()[0]
    for idx, candle in enumerate(fresh):
        candle["_absolute_index"] = int(absolute_offset) + idx
    with conn:
        _replay_cycles(conn, tf, fresh, start_from_existing=True)
    conn.close()


def _replay_cycles(conn: sqlite3.Connection, tf: str, candles: list[dict], start_from_existing: bool) -> None:
    active = _active_cycles_from_db(conn, tf) if start_from_existing else []
    for idx, candle in enumerate(candles):
        current_index = int(candle.get("_absolute_index", idx))

        still_open: list[dict] = []
        for cycle in active:
            origin_candle = _cycle_origin_candle(cycle)
            cycle_type = str(cycle["cycle_type"])
            if _check_seeker_div(origin_candle, cycle_type, candle):
                cycle["div_count_total"] = int(cycle["div_count_total"] or 0) + 1
                cycle["last_div_ts"] = int(candle["timestamp"])
                if cycle.get("time_to_first_div_bars") is None:
                    cycle["time_to_first_div_bars"] = current_index - int(cycle["origin_index"])
                    cycle["time_to_first_div_ms"] = int(candle["timestamp"]) - int(cycle["origin_ts"])
                cycle["last_event_ts"] = int(candle["timestamp"])
                _insert_event(conn, _event_payload(cycle, tf, "div", candle, current_index))

            if _check_seeker_kill(origin_candle, cycle_type, candle):
                cycle["status"] = "killed"
                cycle["last_kill_ts"] = int(candle["timestamp"])
                cycle["last_event_ts"] = int(candle["timestamp"])
                cycle["age_bars"] = current_index - int(cycle["origin_index"])
                cycle["age_ms"] = int(candle["timestamp"]) - int(cycle["origin_ts"])
                cycle["time_to_kill_bars"] = cycle["age_bars"]
                cycle["time_to_kill_ms"] = cycle["age_ms"]
                _insert_event(conn, _event_payload(cycle, tf, "kill", candle, current_index))
                _insert_cycle(conn, cycle)
                continue

            cycle["age_bars"] = current_index - int(cycle["origin_index"])
            cycle["age_ms"] = int(candle["timestamp"]) - int(cycle["origin_ts"])
            _insert_cycle(conn, cycle)
            still_open.append(cycle)

        active = still_open

        for cycle_type, origin_flag in (("HS", int(_to_num(candle.get("is_seeker_hs"), 0))), ("LS", int(_to_num(candle.get("is_seeker_ls"), 0)))):
            if not origin_flag:
                continue
            cycle = _origin_cycle_row(tf, candle, cycle_type, current_index)
            _insert_cycle(conn, cycle)
            _insert_event(conn, _event_payload(cycle, tf, "origin", candle, current_index))
            active.append(cycle)


def get_seeker_cycles(tf: str, path: str, status: str | None = None, limit: int = 250) -> list[dict]:
    conn = _connect(path)
    ensure_tables(conn)
    sql = "SELECT * FROM seeker_cycles WHERE timeframe = ?"
    params: list = [tf]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY origin_ts DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, tuple(params)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cycle_context(tf: str, path: str, ts: int | None = None, price: float | None = None) -> dict:
    conn = _connect(path)
    ensure_tables(conn)
    if ts is None:
        row = conn.execute(f"SELECT timestamp, close FROM candles_{tf} ORDER BY timestamp DESC LIMIT 1").fetchone()
        if not row:
            conn.close()
            return {"timeframe": tf, "activeAtTs": None, "price": price, "openCycles": [], "killedCycles": []}
        ts = int(row["timestamp"])
        if price is None:
            price = _to_num(row["close"])
    elif price is None:
        row = conn.execute(f"SELECT close FROM candles_{tf} WHERE timestamp = ?", (ts,)).fetchone()
        if row:
            price = _to_num(row["close"])

    cycles = [dict(r) for r in conn.execute(
        "SELECT * FROM seeker_cycles WHERE timeframe = ? AND origin_ts <= ? ORDER BY origin_ts DESC",
        (tf, ts),
    ).fetchall()]
    state = {"open": [], "killed": []}
    for cycle in cycles:
        last_kill_ts = cycle.get("last_kill_ts")
        effective_status = "killed" if last_kill_ts and int(last_kill_ts) <= ts else "open"
        cycle["effective_status"] = effective_status
        state[effective_status].append(cycle)

    def nearest(predicate):
        candidates = []
        for cycle in predicate:
            zone_top = _to_num(cycle.get("zone_top"))
            zone_bottom = _to_num(cycle.get("zone_bottom"))
            if price is None:
                distance = 0.0
            elif price < zone_bottom:
                distance = zone_bottom - price
            elif price > zone_top:
                distance = price - zone_top
            else:
                distance = 0.0
            candidates.append((distance, cycle))
        candidates.sort(key=lambda item: (item[0], -int(item[1]["origin_ts"])))
        return candidates[0][1] if candidates else None

    price_value = float(price) if price is not None else None
    open_hs_above = [
        c for c in state["open"]
        if c["cycle_type"] == "HS" and price_value is not None and _to_num(c["zone_bottom"]) >= price_value
    ]
    open_ls_below = [
        c for c in state["open"]
        if c["cycle_type"] == "LS" and price_value is not None and _to_num(c["zone_top"]) <= price_value
    ]
    killed_hs_above = [
        c for c in state["killed"]
        if c["cycle_type"] == "HS" and price_value is not None and _to_num(c["zone_bottom"]) >= price_value
    ]
    killed_ls_below = [
        c for c in state["killed"]
        if c["cycle_type"] == "LS" and price_value is not None and _to_num(c["zone_top"]) <= price_value
    ]
    conn.close()
    return {
        "timeframe": tf,
        "activeAtTs": ts,
        "price": price_value,
        "openCycles": state["open"][:12],
        "killedCycles": state["killed"][:12],
        "containingOpenCycles": [
            c for c in state["open"]
            if price_value is not None and _to_num(c.get("zone_bottom")) <= price_value <= _to_num(c.get("zone_top"))
        ][:12],
        "containingKilledCycles": [
            c for c in state["killed"]
            if price_value is not None and _to_num(c.get("zone_bottom")) <= price_value <= _to_num(c.get("zone_top"))
        ][:12],
        "nearestOpenHsAbove": nearest(open_hs_above),
        "nearestOpenLsBelow": nearest(open_ls_below),
        "nearestKilledHsAbove": nearest(killed_hs_above),
        "nearestKilledLsBelow": nearest(killed_ls_below),
    }
