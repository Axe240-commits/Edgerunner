#!/usr/bin/env python3
"""
Research fakeout -> reclaim -> run behaviour with full multi-timeframe context.

This is intentionally a research runner, not a production signal engine.
It uses:
- M1 as the entry layer
- all available higher timeframe candle features
- multiple entry / stop / target models
- ex-post path classification:
  - clean_run
  - reclaimed_run
  - stopped
  - timed_out
  - flip_run_after_stop

Method guards:
- higher timeframe context only joins fully closed candles
  (candle timestamps are open times, so timestamp + tf duration must be <= signal ts)
- round-trip costs (fee + slippage bps per side) are deducted from every trade;
  net expectancy in R is reported next to win rates (timed_out marked to market)
- percentile thresholds and gate/sweep quantiles are estimated on a train
  prefix of the bars (--train-fraction, default 0.65) and frozen afterwards;
  the walk-forward report evaluates only OOS months with train-fixed setup+gate
- win rates carry Wilson 95% intervals
"""

from __future__ import annotations

import argparse
import collections
import importlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from db import DEFAULT_DB_PATH, _connect, _table_name
from research_warehouse import connect_warehouse


RESEARCH_TFS = ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]
HIGHER_TFS = [tf for tf in RESEARCH_TFS if tf != "1m"]

TF_WEIGHT = {
    "1m": 1.0,
    "3m": 1.2,
    "5m": 1.5,
    "10m": 1.8,
    "15m": 2.2,
    "30m": 2.8,
    "1h": 3.5,
    "2h": 4.2,
    "4h": 5.4,
    "1d": 7.0,
    "1w": 9.0,
}

TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "10m": 600_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}

ENTRY_MODELS = {
    "close": 0.0,
    "retrace_25": 0.25,
    "retrace_40": 0.40,
    "retrace_50": 0.50,
}

STOP_MODELS = ("half_candle", "full_candle", "structure")
TARGET_MODELS = {
    "1.5R": 1.5,
    "2.0R": 2.0,
}

TRANSITION_CONTEXT_KEYS = (
    "direction_margin",
    "regime_margin",
    "micro_margin",
    "lifecycle_margin",
    "lifecycle_pressure",
    "lifecycle_maturity",
    "volume_margin",
    "spot_margin",
    "futures_margin",
    "lead_margin",
    "whale_margin",
    "breakout_force",
    "rejection_force",
    "compression_force",
    "reclaim_quality_long",
    "reclaim_quality_short",
)

FEATURE_COLUMNS = (
    "timestamp, open, high, low, close, total_range, body_ratio, wick_ratio, "
    "body_position, delta_pct, vol_vs_ma, cluster_range_atr, cluster_spread, "
    "bos_bull, bos_bear, choch, bull_div, bear_div, bull_div_streak, bear_div_streak, "
    "is_seeker_hs, is_seeker_ls, is_seeker_div_hs, is_seeker_div_ls, seeker_div_nr, "
    "is_seeker_kill, killed_seekers_count, killed_seeker_divs, killed_seekers_age_min, "
    "killed_seekers_age_max, break_depth, swing_age, sw_bullish, same_dir, "
    "bos_body, bos_wick, delta_vs_ma, "
    "spot_volume, spot_delta, futures_volume, futures_delta, futures_minus_spot_volume, futures_minus_spot_delta, "
    "dist_swing_high, dist_swing_low, seeker_zone_size, seeker_zone_vs_body, "
    "seeker_wick_dominance, htf_trend, htf_bos, "
    "whale_sentiment, whale_confidence, bull_pressure, bear_pressure, "
    "whale_cluster, whale_cluster_strength, whale_cluster_dir, elite_whale_active"
)
FEATURE_COLUMN_NAMES = [part.strip() for part in FEATURE_COLUMNS.split(",")]
THRESHOLD_COLUMNS = "timestamp, cluster_range_atr, vol_vs_ma, cluster_spread"
CYCLE_TS_COLUMNS = "timestamp, close"


@dataclass
class TfThresholds:
    tight_cluster: float
    quiet_volume: float
    quiet_spread: float


@dataclass
class TfCursor:
    rows: list[dict]
    index: int = 0
    tf_ms: int = 0

    def advance_to(self, ts: int) -> dict | None:
        # Only fully closed candles may join: candle timestamps are open times,
        # so a candle is known at ts only if timestamp + tf_ms <= ts.
        while self.index + 1 < len(self.rows) and int(self.rows[self.index + 1]["timestamp"]) + self.tf_ms <= ts:
            self.index += 1
        if self.rows and int(self.rows[self.index]["timestamp"]) + self.tf_ms <= ts:
            return self.rows[self.index]
        return None


def using_duckdb(conn: object) -> bool:
    module = conn.__class__.__module__
    return module.startswith("duckdb") or module.startswith("_duckdb")


def query_rows(conn: object, sql: str, params: tuple | list = ()) -> list[dict]:
    if using_duckdb(conn):
        return conn.execute(sql, params).to_arrow_table().to_pylist()
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def query_row(conn: object, sql: str, params: tuple | list = ()) -> dict | None:
    rows = query_rows(conn, sql, params)
    return rows[0] if rows else None


def connect_research_backend(db_path: str, use_warehouse: bool = False):
    if use_warehouse or db_path.endswith(".duckdb"):
        return connect_warehouse(db_path)
    return _connect(db_path)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * p))
    return float(ordered[max(0, min(len(ordered) - 1, idx))])


def wilson_ci95(wins: int, n: int) -> list[float]:
    """Wilson 95% interval for a win rate, returned as [lo, hi] in percent."""
    if n <= 0:
        return [0.0, 0.0]
    z = 1.96
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, center - margin) * 100.0, 2), round(min(1.0, center + margin) * 100.0, 2)]


def to_num(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def band_score(value: float, low: float, peak: float, high: float) -> float:
    if value <= low or value >= high:
        return 0.0
    if value == peak:
        return 1.0
    if value < peak:
        return (value - low) / max(1e-9, peak - low)
    return (high - value) / max(1e-9, high - peak)


def unique_quantile_thresholds(values: list[float]) -> list[float]:
    if not values:
        return []
    thresholds = {
        round(percentile(values, q), 4)
        for q in (0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85)
    }
    return sorted(thresholds)


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone().strftime("%d.%m.%y %H:%M")


def result_rank(result: str) -> int:
    return {
        "clean_run": 4,
        "reclaimed_run": 3,
        "timed_out": 2,
        "stopped": 1,
    }.get(result, 0)


def load_timeframe_rows(
    conn: object,
    tf: str,
    since_ts: int | None = None,
    until_ts: int | None = None,
    columns: str = FEATURE_COLUMNS,
) -> list[dict]:
    table = _table_name(tf)
    if since_ts is None and until_ts is None:
        return query_rows(
            conn,
            f"SELECT {columns} FROM {table} ORDER BY timestamp"
        )
    clauses = []
    params: list[int] = []
    if since_ts is not None:
        clauses.append("timestamp >= ?")
        params.append(since_ts)
    if until_ts is not None:
        clauses.append("timestamp <= ?")
        params.append(until_ts)
    where = " AND ".join(clauses)
    return query_rows(
        conn,
        f"SELECT {columns} FROM {table} WHERE {where} ORDER BY timestamp",
        tuple(params),
    )


def build_joined_warehouse_rows(
    conn: object,
    since_ts: int | None = None,
    until_ts: int | None = None,
) -> list[dict]:
    m1_where = []
    params: list[int] = []
    if since_ts is not None:
        m1_where.append("timestamp >= ?")
        params.append(since_ts)
    if until_ts is not None:
        m1_where.append("timestamp <= ?")
        params.append(until_ts)
    base_where = f"WHERE {' AND '.join(m1_where)}" if m1_where else ""

    select_parts = [f"m1.{col}" for col in FEATURE_COLUMN_NAMES]
    join_parts = []
    for tf in HIGHER_TFS:
        alias = f"tf_{tf.replace('m', 'm').replace('h', 'h').replace('d', 'd').replace('w', 'w')}"
        table = _table_name(tf)
        tf_select = ", ".join(
            [f'{alias}.{col} AS "{tf}__{col}"' for col in FEATURE_COLUMN_NAMES]
        )
        select_parts.append(tf_select)
        join_parts.append(
            f"""
            ASOF LEFT JOIN (
                SELECT {FEATURE_COLUMNS}
                FROM {table}
                ORDER BY timestamp
            ) AS {alias}
            ON m1.timestamp >= {alias}.timestamp + {TF_MS[tf]}
            """
        )

    sql = f"""
        SELECT
            {", ".join(select_parts)}
        FROM (
            SELECT {FEATURE_COLUMNS}
            FROM candles_1m
            {base_where}
            ORDER BY timestamp
        ) AS m1
        {' '.join(join_parts)}
        ORDER BY m1.timestamp
    """
    return query_rows(conn, sql, params)


def split_joined_mtf_rows(rows: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    rows_1m: list[dict] = []
    rows_by_tf: dict[str, list[dict]] = {tf: [] for tf in HIGHER_TFS}
    seen_ts: dict[str, set[int]] = {tf: set() for tf in HIGHER_TFS}

    for row in rows:
        base = {col: row[col] for col in FEATURE_COLUMN_NAMES}
        rows_1m.append(base)
        for tf in HIGHER_TFS:
            prefix = f"{tf}__"
            tf_row = {col: row.get(f"{prefix}{col}") for col in FEATURE_COLUMN_NAMES}
            ts = tf_row.get("timestamp")
            if ts is None:
                continue
            ts_int = int(ts)
            if ts_int in seen_ts[tf]:
                continue
            seen_ts[tf].add(ts_int)
            rows_by_tf[tf].append(tf_row)
    return rows_1m, rows_by_tf


def mtf_context_from_joined_row(row: dict) -> dict[str, dict] | None:
    mtf_rows: dict[str, dict] = {}
    for tf in HIGHER_TFS:
        prefix = f"{tf}__"
        ts = row.get(f"{prefix}timestamp")
        if ts is None:
            mtf_rows[tf] = None
            continue
        mtf_rows[tf] = {col: row.get(f"{prefix}{col}") for col in FEATURE_COLUMN_NAMES}
    return mtf_rows


def mtf_timestamps_from_joined_row(row: dict) -> dict[str, int | None]:
    mtf_ts: dict[str, int | None] = {}
    for tf in HIGHER_TFS:
        prefix = f"{tf}__"
        ts = row.get(f"{prefix}timestamp")
        mtf_ts[tf] = int(ts) if ts is not None else None
    return mtf_ts


def mtf_timestamps_from_rows(mtf_rows: dict[str, dict | None]) -> dict[str, int | None]:
    return {
        tf: (int(tf_row["timestamp"]) if tf_row is not None else None)
        for tf, tf_row in mtf_rows.items()
    }


def build_thresholds(rows: Iterable[dict]) -> TfThresholds:
    cluster = [to_num(row["cluster_range_atr"]) for row in rows if row["cluster_range_atr"] is not None]
    volume = [to_num(row["vol_vs_ma"]) for row in rows if row["vol_vs_ma"] is not None]
    spread = [to_num(row["cluster_spread"]) for row in rows if row["cluster_spread"] is not None]
    return TfThresholds(
        tight_cluster=percentile(cluster, 0.35),
        quiet_volume=percentile(volume, 0.50),
        quiet_spread=percentile(spread, 0.40),
    )


def row_tight_range(row: dict, thresholds: TfThresholds) -> bool:
    return (
        to_num(row["cluster_range_atr"]) <= thresholds.tight_cluster
        and to_num(row["vol_vs_ma"], 1.0) <= thresholds.quiet_volume
        and to_num(row["cluster_spread"]) <= thresholds.quiet_spread
    )


def score_snapshot(tf: str, row: dict, thresholds: TfThresholds) -> dict[str, float]:
    weight = TF_WEIGHT[tf]
    bull = 0.0
    bear = 0.0
    compression = 0.0
    event = 0.0

    if row_tight_range(row, thresholds):
        compression += 1.0 * weight
    if to_num(row["cluster_range_atr"]) <= thresholds.tight_cluster:
        compression += 0.35 * weight
    if to_num(row["vol_vs_ma"], 1.0) <= thresholds.quiet_volume:
        compression += 0.25 * weight

    if int(to_num(row["bos_bull"])) == 1:
        bull += 1.25 * weight
    if int(to_num(row["bos_bear"])) == 1:
        bear += 1.25 * weight
    if int(to_num(row["choch"])) == 1:
        if to_num(row["delta_pct"]) >= 0:
            bull += 0.55 * weight
        else:
            bear += 0.55 * weight
    if int(to_num(row["sw_bullish"])) == 1:
        bull += 0.75 * weight
    if int(to_num(row["bull_div"])) == 1:
        bull += (0.6 + 0.08 * max(0.0, to_num(row["bull_div_streak"]) - 1.0)) * weight
    if int(to_num(row["bear_div"])) == 1:
        bear += (0.6 + 0.08 * max(0.0, to_num(row["bear_div_streak"]) - 1.0)) * weight
    if int(to_num(row["is_seeker_div_ls"])) == 1:
        bull += (0.85 + 0.05 * min(12.0, to_num(row["seeker_div_nr"]))) * weight
    if int(to_num(row["is_seeker_div_hs"])) == 1:
        bear += (0.85 + 0.05 * min(12.0, to_num(row["seeker_div_nr"]))) * weight
    if int(to_num(row["is_seeker_ls"])) == 1:
        bull += 0.45 * weight
        event += 0.25 * weight
    if int(to_num(row["is_seeker_hs"])) == 1:
        bear += 0.45 * weight
        event += 0.25 * weight
    if int(to_num(row["is_seeker_kill"])) == 1:
        event += (0.55 + 0.08 * min(6.0, to_num(row["killed_seekers_count"]))) * weight

    if to_num(row["delta_pct"]) > 0:
        bull += 0.18 * weight
    elif to_num(row["delta_pct"]) < 0:
        bear += 0.18 * weight

    if to_num(row["futures_minus_spot_volume"]) > 0:
        bull += 0.22 * weight
    elif to_num(row["futures_minus_spot_volume"]) < 0:
        bear += 0.22 * weight

    if to_num(row["break_depth"]) > 0:
        if int(to_num(row["bos_bull"])) == 1:
            bull += 0.18 * weight * min(2.0, to_num(row["break_depth"]))
        if int(to_num(row["bos_bear"])) == 1:
            bear += 0.18 * weight * min(2.0, to_num(row["break_depth"]))

    return {
        "bull": bull,
        "bear": bear,
        "compression": compression,
        "event": event,
        "tight_range": 1.0 if row_tight_range(row, thresholds) else 0.0,
    }


def cycle_metrics(conn: object, tf: str, ts: int, price: float) -> dict[str, float]:
    row = query_row(
        conn,
        """
        WITH active AS (
            SELECT
                cycle_type,
                zone_top,
                zone_bottom,
                div_count_total,
                age_bars,
                age_ms,
                CASE
                    WHEN last_kill_ts IS NOT NULL AND last_kill_ts + ? <= ? THEN 'killed'
                    ELSE 'open'
                END AS eff_status
            FROM seeker_cycles
            WHERE timeframe = ?
              AND origin_ts + ? <= ?
        )
        SELECT
            COALESCE(SUM(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom <= ? AND zone_top >= ? THEN 1 ELSE 0 END), 0) AS inside_open_hs,
            COALESCE(SUM(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_bottom <= ? AND zone_top >= ? THEN 1 ELSE 0 END), 0) AS inside_open_ls,
            COALESCE(SUM(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom <= ? AND zone_top >= ? THEN 1 ELSE 0 END), 0) AS inside_killed_hs,
            COALESCE(SUM(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_bottom <= ? AND zone_top >= ? THEN 1 ELSE 0 END), 0) AS inside_killed_ls,
            MIN(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom >= ? THEN zone_bottom - ? END) AS dist_open_hs_above,
            MIN(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_top <= ? THEN ? - zone_top END) AS dist_open_ls_below,
            MIN(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom >= ? THEN zone_bottom - ? END) AS dist_killed_hs_above,
            MIN(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_top <= ? THEN ? - zone_top END) AS dist_killed_ls_below,
            COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom >= ? THEN div_count_total ELSE 0 END), 0) AS nearest_open_hs_divs_above,
            COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_top <= ? THEN div_count_total ELSE 0 END), 0) AS nearest_open_ls_divs_below,
            COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom >= ? THEN age_bars ELSE 0 END), 0) AS nearest_killed_hs_age_bars_above,
            COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_top <= ? THEN age_bars ELSE 0 END), 0) AS nearest_killed_ls_age_bars_below,
            COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom <= ? AND zone_top >= ? THEN div_count_total ELSE 0 END), 0) AS max_open_hs_divs,
            COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_bottom <= ? AND zone_top >= ? THEN div_count_total ELSE 0 END), 0) AS max_open_ls_divs,
            COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom <= ? AND zone_top >= ? THEN age_bars ELSE 0 END), 0) AS max_killed_hs_age_bars,
            COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_bottom <= ? AND zone_top >= ? THEN age_bars ELSE 0 END), 0) AS max_killed_ls_age_bars
        FROM active
        """,
        [TF_MS[tf], ts, tf, TF_MS[tf], ts] + [price] * 28,
    )
    if not row:
        return {}
    return {key: to_num(value) for key, value in row.items()}


def build_cycle_metric_cache_warehouse(
    conn: object,
    rows_by_tf: dict[str, list[dict]],
) -> dict[str, dict[int, dict[str, float]]]:
    pa = importlib.import_module("pyarrow")
    cache: dict[str, dict[int, dict[str, float]]] = {}

    for tf, rows in rows_by_tf.items():
        probes = [
            {"timestamp": int(row["timestamp"]), "price": to_num(row["close"])}
            for row in rows
            if row.get("timestamp") is not None
        ]
        if not probes:
            cache[tf] = {}
            continue

        view_name = f"probe_{tf.replace('m', 'm').replace('h', 'h').replace('d', 'd').replace('w', 'w')}"
        conn.register(view_name, pa.Table.from_pylist(probes))
        tf_sql = tf.replace("'", "''")
        result_rows = query_rows(
            conn,
            f"""
            WITH joined AS (
                SELECT
                    p.timestamp,
                    p.price,
                    sc.cycle_type,
                    sc.zone_top,
                    sc.zone_bottom,
                    sc.div_count_total,
                    sc.age_bars,
                    CASE
                        WHEN sc.last_kill_ts IS NOT NULL AND sc.last_kill_ts + {TF_MS[tf]} <= p.timestamp THEN 'killed'
                        ELSE 'open'
                    END AS eff_status
                FROM {view_name} AS p
                LEFT JOIN seeker_cycles AS sc
                  ON sc.timeframe = '{tf_sql}'
                 AND sc.origin_ts + {TF_MS[tf]} <= p.timestamp
            )
            SELECT
                timestamp,
                COALESCE(SUM(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom <= price AND zone_top >= price THEN 1 ELSE 0 END), 0) AS inside_open_hs,
                COALESCE(SUM(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_bottom <= price AND zone_top >= price THEN 1 ELSE 0 END), 0) AS inside_open_ls,
                COALESCE(SUM(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom <= price AND zone_top >= price THEN 1 ELSE 0 END), 0) AS inside_killed_hs,
                COALESCE(SUM(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_bottom <= price AND zone_top >= price THEN 1 ELSE 0 END), 0) AS inside_killed_ls,
                MIN(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom >= price THEN zone_bottom - price END) AS dist_open_hs_above,
                MIN(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_top <= price THEN price - zone_top END) AS dist_open_ls_below,
                MIN(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom >= price THEN zone_bottom - price END) AS dist_killed_hs_above,
                MIN(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_top <= price THEN price - zone_top END) AS dist_killed_ls_below,
                COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom >= price THEN div_count_total ELSE 0 END), 0) AS nearest_open_hs_divs_above,
                COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_top <= price THEN div_count_total ELSE 0 END), 0) AS nearest_open_ls_divs_below,
                COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom >= price THEN age_bars ELSE 0 END), 0) AS nearest_killed_hs_age_bars_above,
                COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_top <= price THEN age_bars ELSE 0 END), 0) AS nearest_killed_ls_age_bars_below,
                COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom <= price AND zone_top >= price THEN div_count_total ELSE 0 END), 0) AS max_open_hs_divs,
                COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_bottom <= price AND zone_top >= price THEN div_count_total ELSE 0 END), 0) AS max_open_ls_divs,
                COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom <= price AND zone_top >= price THEN age_bars ELSE 0 END), 0) AS max_killed_hs_age_bars,
                COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_bottom <= price AND zone_top >= price THEN age_bars ELSE 0 END), 0) AS max_killed_ls_age_bars
            FROM joined
            GROUP BY timestamp, price
            ORDER BY timestamp
            """,
        )
        conn.unregister(view_name)

        tf_cache: dict[int, dict[str, float]] = {}
        for item in result_rows:
            ts = int(item["timestamp"])
            tf_cache[ts] = {key: to_num(value) for key, value in item.items() if key != "timestamp"}
        cache[tf] = tf_cache

    return cache


def build_cycle_metric_cache(
    conn: object,
    rows_by_tf: dict[str, list[dict]],
) -> dict[str, dict[int, dict[str, float]]]:
    if using_duckdb(conn):
        return build_cycle_metric_cache_warehouse(conn, rows_by_tf)
    cache: dict[str, dict[int, dict[str, float]]] = {}
    for tf, rows in rows_by_tf.items():
        tf_cache: dict[int, dict[str, float]] = {}
        for row in rows:
            ts = int(row["timestamp"])
            if ts in tf_cache:
                continue
            tf_cache[ts] = cycle_metrics(conn, tf, ts, to_num(row["close"]))
        cache[tf] = tf_cache
    return cache


def build_snapshot_score_cache(
    rows_by_tf: dict[str, list[dict]],
    thresholds: dict[str, TfThresholds],
) -> dict[str, dict[int, dict[str, float]]]:
    cache: dict[str, dict[int, dict[str, float]]] = {}
    for tf, rows in rows_by_tf.items():
        tf_cache: dict[int, dict[str, float]] = {}
        tf_thresholds = thresholds[tf]
        for row in rows:
            ts = int(row["timestamp"])
            if ts in tf_cache:
                continue
            tf_cache[ts] = score_snapshot(tf, row, tf_thresholds)
        cache[tf] = tf_cache
    return cache


def build_volume_breaker_score_cache(
    rows_by_tf: dict[str, list[dict]],
) -> dict[str, dict[int, dict[str, float]]]:
    cache: dict[str, dict[int, dict[str, float]]] = {}
    for tf, rows in rows_by_tf.items():
        tf_cache: dict[int, dict[str, float]] = {}
        for row in rows:
            ts = int(row["timestamp"])
            if ts in tf_cache:
                continue
            tf_cache[ts] = volume_breaker_score(tf, row)
        cache[tf] = tf_cache
    return cache


def build_cycle_state_score_cache(
    cycle_metric_cache: dict[str, dict[int, dict[str, float]]],
) -> dict[str, dict[int, dict[str, float]]]:
    cache: dict[str, dict[int, dict[str, float]]] = {}
    for tf, tf_metrics in cycle_metric_cache.items():
        tf_cache: dict[int, dict[str, float]] = {}
        for ts, metrics in tf_metrics.items():
            score = cycle_score(tf, metrics)
            lifecycle = lifecycle_score(tf, metrics)
            tf_cache[ts] = {**metrics, **score, **lifecycle}
        cache[tf] = tf_cache
    return cache


def cycle_score(tf: str, metrics: dict[str, float]) -> dict[str, float]:
    weight = TF_WEIGHT[tf]
    regime_bull = 0.0
    regime_bear = 0.0
    micro_bull = 0.0
    micro_bear = 0.0
    conflict = 0.0

    if metrics["inside_open_ls"] > 0:
        regime_bull += weight * (0.85 + 0.12 * min(6.0, metrics["inside_open_ls"]))
    if metrics["inside_open_hs"] > 0:
        regime_bear += weight * (0.85 + 0.12 * min(6.0, metrics["inside_open_hs"]))

    if metrics["inside_killed_ls"] > 0:
        micro_bull += weight * (1.15 + 0.15 * min(6.0, metrics["inside_killed_ls"]))
    if metrics["inside_killed_hs"] > 0:
        micro_bear += weight * (1.15 + 0.15 * min(6.0, metrics["inside_killed_hs"]))

    if metrics["max_open_ls_divs"] > 0:
        regime_bull += weight * 0.03 * min(18.0, metrics["max_open_ls_divs"])
    if metrics["max_open_hs_divs"] > 0:
        regime_bear += weight * 0.03 * min(18.0, metrics["max_open_hs_divs"])

    for key, target in (
        ("dist_open_hs_above", "regime_bear"),
        ("dist_open_ls_below", "regime_bull"),
        ("dist_killed_hs_above", "micro_bear"),
        ("dist_killed_ls_below", "micro_bull"),
    ):
        distance = metrics.get(key)
        if distance is None or distance <= 0:
            continue
        distance_boost = 0.0
        if distance <= 25:
            distance_boost = 0.65 * weight
        elif distance <= 75:
            distance_boost = 0.4 * weight
        elif distance <= 175:
            distance_boost = 0.2 * weight
        if target == "regime_bear":
            regime_bear += distance_boost
        elif target == "regime_bull":
            regime_bull += distance_boost
        elif target == "micro_bear":
            micro_bear += distance_boost
        else:
            micro_bull += distance_boost

    if (metrics["inside_open_ls"] > 0 or metrics["inside_killed_ls"] > 0) and (
        metrics["inside_open_hs"] > 0 or metrics["inside_killed_hs"] > 0
    ):
        conflict += 0.6 * weight

    return {
        "regime_bull": regime_bull,
        "regime_bear": regime_bear,
        "micro_bull": micro_bull,
        "micro_bear": micro_bear,
        "conflict": conflict,
    }


def lifecycle_score(tf: str, metrics: dict[str, float]) -> dict[str, float]:
    weight = TF_WEIGHT[tf]
    bull = 0.0
    bear = 0.0
    pressure = 0.0
    maturity = 0.0

    open_ls_divs = metrics.get("max_open_ls_divs", 0.0)
    open_hs_divs = metrics.get("max_open_hs_divs", 0.0)
    killed_ls_age = metrics.get("max_killed_ls_age_bars", 0.0)
    killed_hs_age = metrics.get("max_killed_hs_age_bars", 0.0)
    nearest_open_ls_divs = metrics.get("nearest_open_ls_divs_below", 0.0)
    nearest_open_hs_divs = metrics.get("nearest_open_hs_divs_above", 0.0)
    nearest_killed_ls_age = metrics.get("nearest_killed_ls_age_bars_below", 0.0)
    nearest_killed_hs_age = metrics.get("nearest_killed_hs_age_bars_above", 0.0)
    dist_open_ls = metrics.get("dist_open_ls_below", 0.0)
    dist_open_hs = metrics.get("dist_open_hs_above", 0.0)
    dist_killed_ls = metrics.get("dist_killed_ls_below", 0.0)
    dist_killed_hs = metrics.get("dist_killed_hs_above", 0.0)

    if open_ls_divs > 0:
        bull += weight * 0.06 * min(18.0, open_ls_divs)
        pressure += weight * 0.05 * min(18.0, open_ls_divs)
    if open_hs_divs > 0:
        bear += weight * 0.06 * min(18.0, open_hs_divs)
        pressure += weight * 0.05 * min(18.0, open_hs_divs)

    if metrics["inside_killed_ls"] > 0 and killed_ls_age > 0:
        freshness = 1.0 if killed_ls_age <= 6 else 0.55 if killed_ls_age <= 24 else 0.25
        bull += weight * (0.45 + freshness * 0.55)
        maturity += weight * freshness
    if metrics["inside_killed_hs"] > 0 and killed_hs_age > 0:
        freshness = 1.0 if killed_hs_age <= 6 else 0.55 if killed_hs_age <= 24 else 0.25
        bear += weight * (0.45 + freshness * 0.55)
        maturity += weight * freshness

    if metrics["inside_open_ls"] > 0 and open_ls_divs >= 4:
        bull += weight * 0.2
        pressure += weight * 0.2
    if metrics["inside_open_hs"] > 0 and open_hs_divs >= 4:
        bear += weight * 0.2
        pressure += weight * 0.2

    if dist_open_ls > 0 and dist_open_ls <= 175 and nearest_open_ls_divs > 0:
        bull += weight * 0.03 * min(15.0, nearest_open_ls_divs)
        pressure += weight * 0.02 * min(15.0, nearest_open_ls_divs)
    if dist_open_hs > 0 and dist_open_hs <= 175 and nearest_open_hs_divs > 0:
        bear += weight * 0.03 * min(15.0, nearest_open_hs_divs)
        pressure += weight * 0.02 * min(15.0, nearest_open_hs_divs)

    if dist_killed_ls > 0 and dist_killed_ls <= 125 and nearest_killed_ls_age > 0:
        freshness = 1.0 if nearest_killed_ls_age <= 6 else 0.55 if nearest_killed_ls_age <= 24 else 0.25
        bull += weight * (0.12 + freshness * 0.18)
        maturity += weight * freshness * 0.5
    if dist_killed_hs > 0 and dist_killed_hs <= 125 and nearest_killed_hs_age > 0:
        freshness = 1.0 if nearest_killed_hs_age <= 6 else 0.55 if nearest_killed_hs_age <= 24 else 0.25
        bear += weight * (0.12 + freshness * 0.18)
        maturity += weight * freshness * 0.5

    return {
        "bull": bull,
        "bear": bear,
        "pressure": pressure,
        "maturity": maturity,
    }


def volume_breaker_score(tf: str, row: sqlite3.Row) -> dict[str, float]:
    weight = TF_WEIGHT[tf]
    bull = 0.0
    bear = 0.0
    breakout = 0.0
    rejection = 0.0
    compression = 0.0
    exhaustion = 0.0

    spot_bull = 0.0
    spot_bear = 0.0
    futures_bull = 0.0
    futures_bear = 0.0
    lead_bull = 0.0
    lead_bear = 0.0
    whale_bull = 0.0
    whale_bear = 0.0

    vol_vs_ma = to_num(row["vol_vs_ma"], 1.0)
    futures_lead = to_num(row["futures_minus_spot_volume"])
    futures_delta_lead = to_num(row["futures_minus_spot_delta"])
    delta_pct = to_num(row["delta_pct"])
    bos_body = int(to_num(row["bos_body"]))
    bos_wick = int(to_num(row["bos_wick"]))
    dist_high = to_num(row["dist_swing_high"])
    dist_low = to_num(row["dist_swing_low"])
    wick_ratio = to_num(row["wick_ratio"])
    body_ratio = to_num(row["body_ratio"])
    same_dir = int(to_num(row["same_dir"]))
    spot_volume = to_num(row["spot_volume"])
    spot_delta = to_num(row["spot_delta"])
    futures_volume = to_num(row["futures_volume"])
    futures_delta = to_num(row["futures_delta"])
    whale_sentiment = to_num(row["whale_sentiment"])
    whale_confidence = clamp(to_num(row["whale_confidence"]), 0.0, 1.0)
    bull_pressure = to_num(row["bull_pressure"])
    bear_pressure = to_num(row["bear_pressure"])
    whale_cluster = int(to_num(row["whale_cluster"]))
    whale_cluster_strength = clamp(to_num(row["whale_cluster_strength"]), 0.0, 1.0)
    whale_cluster_dir = to_num(row["whale_cluster_dir"])
    elite_whale_active = int(to_num(row["elite_whale_active"]))
    spot_to_futures_ratio = spot_volume / max(1.0, futures_volume) if futures_volume > 0 else 0.0
    futures_to_spot_ratio = futures_volume / max(1.0, spot_volume) if spot_volume > 0 else 0.0

    if vol_vs_ma <= 0.95:
        compression += 0.22 * weight
    if vol_vs_ma <= 0.8:
        compression += 0.28 * weight

    if vol_vs_ma >= 1.25:
        breakout += 0.45 * weight
        bull += 0.12 * weight if delta_pct > 0 else 0.0
        bear += 0.12 * weight if delta_pct < 0 else 0.0
    if vol_vs_ma >= 1.6:
        breakout += 0.3 * weight

    if futures_lead > 0:
        bull += 0.2 * weight
        lead_bull += 0.2 * weight
    elif futures_lead < 0:
        bear += 0.2 * weight
        lead_bear += 0.2 * weight

    if futures_volume > spot_volume * 1.8 and futures_volume > 0:
        if futures_lead > 0:
            bull += 0.15 * weight
            futures_bull += 0.15 * weight
            lead_bull += 0.18 * weight
        elif futures_lead < 0:
            bear += 0.15 * weight
            futures_bear += 0.15 * weight
            lead_bear += 0.18 * weight

    if spot_delta > 0:
        spot_bull += 0.12 * weight * clamp(abs(spot_delta) / max(1.0, spot_volume), 0.0, 1.0)
    elif spot_delta < 0:
        spot_bear += 0.12 * weight * clamp(abs(spot_delta) / max(1.0, spot_volume), 0.0, 1.0)

    if futures_delta > 0:
        futures_bull += 0.16 * weight * clamp(abs(futures_delta) / max(1.0, futures_volume), 0.0, 1.0)
    elif futures_delta < 0:
        futures_bear += 0.16 * weight * clamp(abs(futures_delta) / max(1.0, futures_volume), 0.0, 1.0)

    if spot_to_futures_ratio >= 1.2 and spot_volume > 0:
        if spot_delta >= 0:
            lead_bull += 0.14 * weight
        else:
            lead_bear += 0.14 * weight

    if futures_to_spot_ratio >= 1.8 and futures_volume > 0:
        if futures_delta >= 0:
            lead_bull += 0.14 * weight
        else:
            lead_bear += 0.14 * weight

    if futures_delta_lead > 0:
        lead_bull += 0.08 * weight
    elif futures_delta_lead < 0:
        lead_bear += 0.08 * weight

    whale_bias = (bull_pressure - bear_pressure) + whale_sentiment * 2.0
    if whale_bias > 0:
        whale_bull += (0.12 + 0.18 * whale_confidence) * weight
    elif whale_bias < 0:
        whale_bear += (0.12 + 0.18 * whale_confidence) * weight

    if whale_cluster and whale_cluster_strength > 0:
        cluster_boost = (0.08 + 0.16 * whale_cluster_strength) * weight
        if whale_cluster_dir > 0:
            whale_bull += cluster_boost
        elif whale_cluster_dir < 0:
            whale_bear += cluster_boost

    if elite_whale_active:
        if whale_bias >= 0:
            whale_bull += 0.12 * weight
        else:
            whale_bear += 0.12 * weight

    if delta_pct > 0.25:
        bull += 0.18 * weight
    elif delta_pct < -0.25:
        bear += 0.18 * weight

    if int(to_num(row["bos_bull"])) == 1:
        if bos_body == 1:
            bull += 0.45 * weight
            breakout += 0.35 * weight
        elif bos_wick == 1:
            bull += 0.12 * weight
            rejection += 0.18 * weight
        if dist_high >= 0.75:
            bull += 0.18 * weight
    if int(to_num(row["bos_bear"])) == 1:
        if bos_body == 1:
            bear += 0.45 * weight
            breakout += 0.35 * weight
        elif bos_wick == 1:
            bear += 0.12 * weight
            rejection += 0.18 * weight
        if dist_low >= 0.75:
            bear += 0.18 * weight

    if bos_wick == 1 and wick_ratio >= 0.55 and vol_vs_ma >= 1.2:
        rejection += 0.28 * weight

    if body_ratio <= 0.3 and wick_ratio >= 0.55 and vol_vs_ma >= 1.3 and same_dir == 0:
        exhaustion += 0.25 * weight

    bull += spot_bull + futures_bull + lead_bull + whale_bull
    bear += spot_bear + futures_bear + lead_bear + whale_bear

    return {
        "bull": bull,
        "bear": bear,
        "breakout": breakout,
        "rejection": rejection,
        "compression": compression,
        "exhaustion": exhaustion,
        "spot_bull": spot_bull,
        "spot_bear": spot_bear,
        "futures_bull": futures_bull,
        "futures_bear": futures_bear,
        "lead_bull": lead_bull,
        "lead_bear": lead_bear,
        "whale_bull": whale_bull,
        "whale_bear": whale_bear,
    }


def reclaim_quality_score(
    direction: str,
    aggregate: dict[str, object],
    cycle_total: dict[str, object],
    volume_total: dict[str, object],
) -> float:
    direction_margin = float(aggregate["direction_margin"])
    regime_margin = float(cycle_total["regime_bull"]) - float(cycle_total["regime_bear"])
    micro_margin = float(cycle_total["micro_bull"]) - float(cycle_total["micro_bear"])
    lifecycle_margin = float(cycle_total["lifecycle_bull"]) - float(cycle_total["lifecycle_bear"])
    lifecycle_pressure = float(cycle_total["lifecycle_pressure"])
    lifecycle_maturity = float(cycle_total["lifecycle_maturity"])
    volume_margin = float(volume_total["bull"]) - float(volume_total["bear"])
    breakout_force = float(volume_total["breakout"])
    rejection_force = float(volume_total["rejection"]) + float(volume_total["exhaustion"])
    compression_force = float(aggregate["compression"]) + float(volume_total["compression"])

    sign = 1.0 if direction == "long" else -1.0
    dir_support = direction_margin * sign
    regime_support = regime_margin * sign
    micro_support = micro_margin * sign
    lifecycle_support = lifecycle_margin * sign
    volume_support = volume_margin * sign

    score = 0.0
    # Good reclaim often fires while local pressure still leans against the reclaim direction.
    score += 2.0 * band_score(dir_support, -10.0, -2.5, 2.5)
    score += 2.5 * band_score(micro_support, -26.0, -12.0, -1.0)
    score += 1.5 * band_score(regime_support, -18.0, -8.0, 3.0)
    score += 1.2 * band_score(lifecycle_support, -12.0, -5.0, 2.0)
    score += 1.0 * band_score(lifecycle_pressure, 1.0, 5.0, 12.0)
    score += 1.5 * band_score(lifecycle_maturity, 8.0, 24.0, 45.0)
    score += 1.0 * band_score(volume_support, 4.0, 10.0, 18.0)
    score += 1.4 * band_score(breakout_force, 2.0, 6.0, 10.5)
    score += 0.9 * band_score(rejection_force, 0.15, 0.9, 1.8)
    score += 1.3 * band_score(compression_force, 20.0, 38.0, 60.0)

    # Outcome-guided bias: good long reclaims in the current slices tend to come
    # from mature cycles with enough pressure, but before the state already gets
    # too loud/obvious on direction + volume + compression.
    if direction == "long":
        if (
            lifecycle_maturity >= 26.0
            and lifecycle_pressure >= 3.8
            and dir_support <= 1.5
            and volume_margin <= 11.8
            and compression_force <= 47.0
        ):
            score += 1.6
        if compression_force >= 55.0:
            score -= 0.9
        if volume_margin >= 13.5:
            score -= 0.7
        if lifecycle_maturity < 20.0:
            score -= 0.8
    else:
        if (
            lifecycle_pressure <= 5.5
            and lifecycle_maturity <= 29.5
            and compression_force >= 40.0
            and rejection_force <= 0.9
        ):
            score += 1.2
        if lifecycle_pressure >= 7.5:
            score -= 0.8
        if lifecycle_maturity >= 34.0:
            score -= 0.7

    return score


def determine_signal_family(
    row: sqlite3.Row,
    aggregate: dict[str, float],
    recent_bull_max: float,
    recent_bear_max: float,
    cycle_total: dict[str, float],
    volume_total: dict[str, float],
) -> tuple[str | None, str | None]:
    bull = aggregate["bull"] + cycle_total["regime_bull"] + volume_total["bull"]
    bear = aggregate["bear"] + cycle_total["regime_bear"] + volume_total["bear"]
    compression = aggregate["compression"]
    compression += volume_total["compression"]
    direction_margin = bull - bear
    micro_margin = cycle_total["micro_bull"] - cycle_total["micro_bear"]
    lifecycle_margin = cycle_total["lifecycle_bull"] - cycle_total["lifecycle_bear"]
    conflict = cycle_total["conflict"]
    breakout_force = volume_total["breakout"]
    rejection_force = volume_total["rejection"] + volume_total["exhaustion"]
    maturity = cycle_total["lifecycle_maturity"]
    reclaim_quality_long = reclaim_quality_score("long", aggregate, cycle_total, volume_total)
    reclaim_quality_short = reclaim_quality_score("short", aggregate, cycle_total, volume_total)

    if compression >= 6.0 and conflict <= 3.8:
        if bear >= 12.0 and direction_margin <= -2.0 and micro_margin <= -1.5 and lifecycle_margin <= 0.25:
            return "zone_fade", "short"
        if bull >= 12.0 and direction_margin >= 2.0 and micro_margin >= 1.5 and lifecycle_margin >= -0.25:
            return "zone_fade", "long"

    if int(to_num(row["bos_bull"])) == 1 or int(to_num(row["sw_bullish"])) == 1 or (int(to_num(row["choch"])) == 1 and to_num(row["delta_pct"]) > 0):
        if (
            bull >= 10.5
            and direction_margin >= 1.5
            and recent_bear_max >= 8.0
            and (micro_margin >= 0.35 or breakout_force >= 2.0 or lifecycle_margin >= 0.35 or reclaim_quality_long >= 8.2)
        ):
            return "reclaim_run", "long"
        if bull >= 10.5 and direction_margin >= 1.2 and breakout_force >= 2.2 and (micro_margin >= 0.5 or maturity >= 1.0):
            return "breaker_run", "long"

    if int(to_num(row["bos_bear"])) == 1 or (int(to_num(row["choch"])) == 1 and to_num(row["delta_pct"]) < 0):
        if (
            bear >= 10.5
            and direction_margin <= -1.5
            and recent_bull_max >= 8.0
            and (micro_margin <= -0.35 or breakout_force >= 2.0 or lifecycle_margin <= -0.35 or reclaim_quality_short >= 8.2)
        ):
            return "reclaim_run", "short"
        if bear >= 10.5 and direction_margin <= -1.2 and breakout_force >= 2.2 and (micro_margin <= -0.5 or maturity >= 1.0):
            return "breaker_run", "short"

    if compression >= 5.0 and conflict >= 3.2:
        if cycle_total["micro_bear"] >= 4.0 and bull >= bear and rejection_force >= 0.4:
            return "micro_fakeout", "short"
        if cycle_total["micro_bull"] >= 4.0 and bear >= bull and rejection_force >= 0.4:
            return "micro_fakeout", "long"

    return None, None


def infer_transition_phase(signal: dict[str, object]) -> str:
    direction = str(signal["direction"])
    breakout_force = to_num(signal.get("breakout_force"))
    micro_margin = to_num(signal.get("micro_margin"))
    regime_margin = to_num(signal.get("regime_margin"))
    direction_margin = to_num(signal.get("direction_margin"))
    has_bos = bool(
        int(to_num(signal.get("m1_bos_bull"))) == 1
        or int(to_num(signal.get("m1_bos_bear"))) == 1
    )
    has_body_break = int(to_num(signal.get("m1_bos_body"))) == 1
    has_choch = int(to_num(signal.get("m1_choch"))) == 1
    has_sw_bull = int(to_num(signal.get("m1_sw_bullish"))) == 1
    has_seeker_kill = int(to_num(signal.get("m1_is_seeker_kill"))) == 1
    futures_lead = to_num(signal.get("m1_futures_minus_spot_volume"))

    direction_support = direction_margin if direction == "long" else -direction_margin
    regime_support = regime_margin if direction == "long" else -regime_margin
    micro_support = micro_margin if direction == "long" else -micro_margin
    futures_support = futures_lead if direction == "long" else -futures_lead

    if (
        has_bos
        and (has_choch or has_sw_bull or has_seeker_kill)
        and direction_support >= 0.8
        and (micro_support >= 0.25 or regime_support >= 0.25 or futures_support > 0)
    ):
        return "reclaim_run"

    if has_body_break and breakout_force >= 2.2 and direction_support >= 0.8:
        return "breaker_run"

    if has_bos and breakout_force >= 2.8 and (micro_support >= 0.5 or futures_support > 0):
        return "breaker_run"

    return str(signal["family"])


def find_recent_structure_stop_from_rows(
    rows_1m: list[dict],
    bar_index: int,
    direction: str,
    lookback: int = 8,
) -> float | None:
    start = max(0, bar_index - lookback)
    rows = rows_1m[start:bar_index]
    if not rows:
        return None
    if direction == "long":
        return min(to_num(row["low"]) for row in rows)
    return max(to_num(row["high"]) for row in rows)


def evaluate_trade_on_rows(
    rows_1m: list[dict],
    signal: dict,
    entry_model: str,
    stop_model: str,
    target_model: str,
    horizon_bars: int = 90,
    fill_bars: int = 20,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> dict[str, object]:
    bar_index = int(signal["bar_index"])
    direction = str(signal["direction"])
    signal_close = to_num(signal["close"])
    signal_high = to_num(signal["high"])
    signal_low = to_num(signal["low"])
    signal_range = max(0.5, signal_high - signal_low)
    retrace = ENTRY_MODELS[entry_model]

    if direction == "long":
        entry_price = signal_close - signal_range * retrace
    else:
        entry_price = signal_close + signal_range * retrace

    if stop_model == "half_candle":
        risk = signal_range * 0.5
        stop_price = entry_price - risk if direction == "long" else entry_price + risk
    elif stop_model == "full_candle":
        risk = signal_range
        stop_price = entry_price - risk if direction == "long" else entry_price + risk
    else:
        structure = find_recent_structure_stop_from_rows(rows_1m, bar_index, direction)
        if structure is None:
            return {"filled": False, "result": "no_structure_stop"}
        stop_price = structure
        risk = entry_price - stop_price if direction == "long" else stop_price - entry_price
        if risk <= 0:
            return {"filled": False, "result": "invalid_structure_stop"}

    target_r = TARGET_MODELS[target_model]
    target_price = entry_price + risk * target_r if direction == "long" else entry_price - risk * target_r

    future = rows_1m[bar_index + 1 : bar_index + 1 + horizon_bars]
    if not future:
        return {"filled": False, "result": "no_future"}

    fill_deadline = min(fill_bars, len(future))
    fill_index = None
    for idx in range(fill_deadline):
        row = future[idx]
        high = to_num(row["high"])
        low = to_num(row["low"])
        if direction == "long":
            if low <= entry_price <= high:
                fill_index = idx
                break
        else:
            if low <= entry_price <= high:
                fill_index = idx
                break

    if fill_index is None:
        if entry_model == "close":
            fill_index = 0
            entry_price = signal_close
            if stop_model == "half_candle":
                stop_price = entry_price - signal_range * 0.5 if direction == "long" else entry_price + signal_range * 0.5
                risk = signal_range * 0.5
            elif stop_model == "full_candle":
                stop_price = entry_price - signal_range if direction == "long" else entry_price + signal_range
                risk = signal_range
            else:
                structure = find_recent_structure_stop_from_rows(rows_1m, bar_index, direction)
                if structure is None:
                    return {"filled": False, "result": "no_structure_stop"}
                stop_price = structure
                risk = entry_price - stop_price if direction == "long" else stop_price - entry_price
                if risk <= 0:
                    return {"filled": False, "result": "invalid_structure_stop"}
            target_price = entry_price + risk * target_r if direction == "long" else entry_price - risk * target_r
        else:
            return {"filled": False, "result": "missed_entry"}

    mae_r = 0.0
    mfe_r = 0.0
    stop_hit_index = None
    target_hit_index = None

    for idx in range(fill_index, len(future)):
        row = future[idx]
        high = to_num(row["high"])
        low = to_num(row["low"])

        if direction == "long":
            adverse = max(0.0, entry_price - low)
            favorable = max(0.0, high - entry_price)
        else:
            adverse = max(0.0, high - entry_price)
            favorable = max(0.0, entry_price - low)
        mae_r = max(mae_r, adverse / risk)
        mfe_r = max(mfe_r, favorable / risk)

        if direction == "long":
            stop_hit = low <= stop_price
            target_hit = high >= target_price
        else:
            stop_hit = high >= stop_price
            target_hit = low <= target_price

        if stop_hit and target_hit:
            stop_hit_index = idx
            break
        if stop_hit:
            stop_hit_index = idx
            break
        if target_hit:
            target_hit_index = idx
            break

    result = "timed_out"
    if target_hit_index is not None:
        result = "clean_run" if mae_r <= 0.25 else "reclaimed_run"
    elif stop_hit_index is not None:
        result = "stopped"

    # Round-trip costs (entry + exit) in R. timed_out is marked to market at
    # the last close inside the horizon. A nominal winner that ends at or
    # below 0 R after costs counts as a loss.
    round_trip_cost_r = entry_price * (fee_bps + slippage_bps) * 2.0 / 10_000.0 / risk
    if target_hit_index is not None:
        net_r = target_r - round_trip_cost_r
    elif stop_hit_index is not None:
        net_r = -1.0 - round_trip_cost_r
    else:
        last_close = to_num(future[-1]["close"])
        if direction == "long":
            mtm_r = (last_close - entry_price) / risk
        else:
            mtm_r = (entry_price - last_close) / risk
        net_r = mtm_r - round_trip_cost_r
    if result in ("clean_run", "reclaimed_run") and net_r <= 0.0:
        result = "stopped"

    flip_run_after_stop = False
    if stop_hit_index is not None:
        stop_row = future[stop_hit_index]
        stop_fill = stop_price
        opp_target = stop_fill + risk * 1.5 if direction == "short" else stop_fill - risk * 1.5
        for row in future[stop_hit_index + 1 :]:
            high = to_num(row["high"])
            low = to_num(row["low"])
            if direction == "short" and high >= opp_target:
                flip_run_after_stop = True
                break
            if direction == "long" and low <= opp_target:
                flip_run_after_stop = True
                break

    return {
        "filled": True,
        "result": result,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "risk_pct": risk / entry_price * 100.0,
        "mae_r": mae_r,
        "mfe_r": mfe_r,
        "net_r": net_r,
        "flip_run_after_stop": flip_run_after_stop,
    }


def aggregate_candidate_context(
    row_ts: int,
    mtf_timestamps: dict[str, int | None],
    snapshot_score_cache: dict[str, dict[int, dict[str, float]]],
) -> dict[str, object]:
    total = {"bull": 0.0, "bear": 0.0, "compression": 0.0, "event": 0.0}
    per_tf: dict[str, dict[str, float]] = {}
    for tf, tf_ts in {"1m": row_ts, **mtf_timestamps}.items():
        if tf_ts is None:
            continue
        snapshot = snapshot_score_cache[tf][int(tf_ts)]
        per_tf[tf] = snapshot
        for key in total:
            total[key] += snapshot[key]
    total["direction_margin"] = total["bull"] - total["bear"]
    total["per_tf"] = per_tf
    return total


def aggregate_cycle_context(
    cycle_state_cache: dict[str, dict[int, dict[str, float]]],
    row_ts: int,
    mtf_timestamps: dict[str, int | None],
) -> dict[str, object]:
    total = {
        "regime_bull": 0.0,
        "regime_bear": 0.0,
        "micro_bull": 0.0,
        "micro_bear": 0.0,
        "conflict": 0.0,
        "lifecycle_bull": 0.0,
        "lifecycle_bear": 0.0,
        "lifecycle_pressure": 0.0,
        "lifecycle_maturity": 0.0,
    }
    per_tf: dict[str, dict[str, float]] = {}
    for tf, tf_ts in {"1m": row_ts, **mtf_timestamps}.items():
        if tf_ts is None:
            continue
        scored = cycle_state_cache[tf][int(tf_ts)]
        per_tf[tf] = scored
        total["regime_bull"] += scored["regime_bull"]
        total["regime_bear"] += scored["regime_bear"]
        total["micro_bull"] += scored["micro_bull"]
        total["micro_bear"] += scored["micro_bear"]
        total["conflict"] += scored["conflict"]
        total["lifecycle_bull"] += scored["bull"]
        total["lifecycle_bear"] += scored["bear"]
        total["lifecycle_pressure"] += scored["pressure"]
        total["lifecycle_maturity"] += scored["maturity"]
    total["per_tf"] = per_tf
    return total


def aggregate_volume_breaker_context(
    row_ts: int,
    mtf_timestamps: dict[str, int | None],
    volume_score_cache: dict[str, dict[int, dict[str, float]]],
) -> dict[str, object]:
    total = {
        "bull": 0.0,
        "bear": 0.0,
        "breakout": 0.0,
        "rejection": 0.0,
        "compression": 0.0,
        "exhaustion": 0.0,
        "spot_bull": 0.0,
        "spot_bear": 0.0,
        "futures_bull": 0.0,
        "futures_bear": 0.0,
        "lead_bull": 0.0,
        "lead_bear": 0.0,
        "whale_bull": 0.0,
        "whale_bear": 0.0,
    }
    per_tf: dict[str, dict[str, float]] = {}
    for tf, tf_ts in {"1m": row_ts, **mtf_timestamps}.items():
        if tf_ts is None:
            continue
        score = volume_score_cache[tf][int(tf_ts)]
        per_tf[tf] = score
        for key in total:
            total[key] += score[key]
    total["per_tf"] = per_tf
    return total


def candidate_reason(
    signal_family: str,
    direction: str,
    aggregate: dict[str, object],
    cycle_total: dict[str, object],
    volume_total: dict[str, object],
) -> str:
    pieces = [signal_family.replace("_", " ")]
    per_tf = aggregate["per_tf"]
    strongest = sorted(per_tf.items(), key=lambda item: item[1]["bull"] + item[1]["bear"] + item[1]["compression"], reverse=True)[:3]
    if strongest:
        pieces.append("mtf " + ", ".join(tf for tf, _ in strongest))
    pieces.append(f"bull {aggregate['bull']:.1f}")
    pieces.append(f"bear {aggregate['bear']:.1f}")
    pieces.append(f"compression {aggregate['compression']:.1f}")
    pieces.append(f"reg {cycle_total['regime_bull'] - cycle_total['regime_bear']:.1f}")
    pieces.append(f"micro {cycle_total['micro_bull'] - cycle_total['micro_bear']:.1f}")
    pieces.append(f"life {cycle_total['lifecycle_bull'] - cycle_total['lifecycle_bear']:.1f}")
    pieces.append(f"vol {volume_total['bull'] - volume_total['bear']:.1f}")
    pieces.append(f"rej {volume_total['rejection'] + volume_total['exhaustion']:.1f}")
    pieces.append(f"dir {direction}")
    return " | ".join(pieces)


def classify_transition(first: dict[str, object], second: dict[str, object]) -> str | None:
    first_family = str(first["family"])
    second_family = infer_transition_phase(second)
    first_direction = str(first["direction"])
    second_direction = str(second["direction"])
    second_result = str(second.get("bestResult") or "")

    if first_family == "micro_fakeout" and first_direction != second_direction:
        if second_result in ("clean_run", "reclaimed_run"):
            return f"fakeout_to_reclaim:{first_direction}->{second_direction}"
        if second_family == "reclaim_run":
            return f"fakeout_to_reclaim:{first_direction}->{second_direction}"
        return f"failed_fakeout_flip:{first_direction}->{second_direction}"

    if first_family == "micro_fakeout" and first_direction == second_direction:
        if second_family == "breaker_run" or (
            second_result in ("clean_run", "reclaimed_run") and to_num(second.get("breakout_force")) >= 2.5
        ):
            return f"fakeout_to_breaker:{first_direction}"

    if first_family == "breaker_run" and second_family == "reclaim_run" and first_direction != second_direction:
        return f"{first_family}:{first_direction} -> {second_family}:{second_direction}"
    if first_direction != second_direction:
        return f"flip:{first_family}:{first_direction}->{second_family}:{second_direction}"
    return None


def transition_gate_passes(
    transition_type: str,
    second_signal: dict[str, object],
    gate_profiles: dict[str, dict[str, object]] | None = None,
) -> bool:
    if transition_type == "failed_fakeout_flip:short->long":
        return False
    if transition_type == "failed_fakeout_flip:long->short":
        return False
    profile = (gate_profiles or {}).get(transition_type)
    if not profile:
        return True
    metric = str(profile["metric"])
    mode = str(profile["mode"])
    threshold = float(profile["threshold"])
    value = to_num(second_signal.get(metric))
    if mode == "min":
        return value >= threshold
    if mode == "max":
        return value <= threshold
    return True


def mine_signal_transitions(signals: list[dict[str, object]], max_gap_ms: int = 45 * 60_000) -> list[dict[str, object]]:
    ordered = sorted(signals, key=lambda item: int(item["timestamp"]))
    transitions: list[dict[str, object]] = []
    for idx, first in enumerate(ordered):
        for second in ordered[idx + 1 :]:
            gap = int(second["timestamp"]) - int(first["timestamp"])
            if gap <= 0:
                continue
            if gap > max_gap_ms:
                break
            transition_type = classify_transition(first, second)
            if not transition_type:
                continue
            transitions.append(
                {
                    "type": transition_type,
                    "start": first["time"],
                    "end": second["time"],
                    "gapMin": round(gap / 60_000, 1),
                    "firstSignal": first,
                    "secondSignal": second,
                    "first": {
                        "family": first["family"],
                        "direction": first["direction"],
                        "score": first["score"],
                        "bestResult": first["bestResult"],
                    },
                    "second": {
                        "family": second["family"],
                        "direction": second["direction"],
                        "score": second["score"],
                        "bestResult": second["bestResult"],
                    },
                }
            )
            break
    transitions.sort(key=lambda item: (item["gapMin"], -float(item["first"]["score"]), -float(item["second"]["score"])))
    return transitions


def summarize_transition_gate_keep_rates(
    all_transitions: list[dict[str, object]],
    gated_transitions: list[dict[str, object]],
) -> list[dict[str, object]]:
    all_counts = collections.Counter(str(item["type"]) for item in all_transitions)
    gated_counts = collections.Counter(str(item["type"]) for item in gated_transitions)
    summary: list[dict[str, object]] = []
    for transition_type, total in all_counts.items():
        kept = gated_counts.get(transition_type, 0)
        summary.append(
            {
                "transitionType": transition_type,
                "total": total,
                "kept": kept,
                "keepRate": round(kept / max(1, total) * 100.0, 2),
            }
        )
    summary.sort(key=lambda item: (item["keepRate"], item["kept"], item["total"]), reverse=True)
    return summary


def bucket_month(ts: int) -> str:
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def evaluate_transition_families(
    rows_1m: list[dict],
    transitions: list[dict[str, object]],
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> list[dict[str, object]]:
    buckets: dict[tuple[str, str], dict[str, object]] = {}
    for transition in transitions:
        second_signal = transition["secondSignal"]
        transition_type = str(transition["type"])
        for entry_model in ENTRY_MODELS:
            for stop_model in STOP_MODELS:
                for target_model in TARGET_MODELS:
                    setup = f"{entry_model}|{stop_model}|{target_model}"
                    key = (transition_type, setup)
                    bucket = buckets.setdefault(
                        key,
                        {
                            "transitionType": transition_type,
                            "setup": setup,
                            "count": 0,
                            "filled": 0,
                            "wins": 0,
                            "cleanRuns": 0,
                            "reclaimedRuns": 0,
                            "stopped": 0,
                            "timedOut": 0,
                            "flipRunsAfterStop": 0,
                            "sumRiskPct": 0.0,
                            "sumNetR": 0.0,
                            "sumFirstScore": 0.0,
                            "sumSecondScore": 0.0,
                            "winsCount": 0,
                            "stopsCount": 0,
                            "winContext": {key: 0.0 for key in TRANSITION_CONTEXT_KEYS},
                            "stopContext": {key: 0.0 for key in TRANSITION_CONTEXT_KEYS},
                            "records": [],
                            "samples": [],
                        },
                    )
                    bucket["count"] += 1
                    bucket["sumFirstScore"] += float(transition["first"]["score"])
                    bucket["sumSecondScore"] += float(transition["second"]["score"])
                    outcome = evaluate_trade_on_rows(
                        rows_1m=rows_1m,
                        signal=second_signal,
                        entry_model=entry_model,
                        stop_model=stop_model,
                        target_model=target_model,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                    )
                    if not outcome.get("filled"):
                        continue
                    bucket["filled"] += 1
                    bucket["sumRiskPct"] += float(outcome.get("risk_pct") or 0.0)
                    bucket["sumNetR"] += float(outcome.get("net_r") or 0.0)
                    result = str(outcome["result"])
                    if result in ("clean_run", "reclaimed_run"):
                        bucket["wins"] += 1
                        bucket["winsCount"] += 1
                        for key in TRANSITION_CONTEXT_KEYS:
                            bucket["winContext"][key] += to_num(second_signal.get(key))
                    if result == "clean_run":
                        bucket["cleanRuns"] += 1
                    elif result == "reclaimed_run":
                        bucket["reclaimedRuns"] += 1
                    elif result == "stopped":
                        bucket["stopped"] += 1
                        bucket["stopsCount"] += 1
                        for key in TRANSITION_CONTEXT_KEYS:
                            bucket["stopContext"][key] += to_num(second_signal.get(key))
                    else:
                        bucket["timedOut"] += 1
                    if outcome.get("flip_run_after_stop"):
                        bucket["flipRunsAfterStop"] += 1
                    bucket["records"].append(
                        {
                            "timestamp": int(second_signal["timestamp"]),
                            "time": str(second_signal.get("time") or ""),
                            "result": result,
                            "riskPct": round(float(outcome.get("risk_pct") or 0.0), 4),
                            "netR": round(float(outcome.get("net_r") or 0.0), 4),
                            "flipAfterStop": bool(outcome.get("flip_run_after_stop")),
                            **{key: to_num(second_signal.get(key)) for key in TRANSITION_CONTEXT_KEYS},
                        }
                    )
                    if len(bucket["samples"]) < 4:
                        bucket["samples"].append(
                            {
                                "start": transition["start"],
                                "end": transition["end"],
                                "gapMin": transition["gapMin"],
                                "first": transition["first"],
                                "second": transition["second"],
                                "result": result,
                                "riskPct": round(float(outcome.get("risk_pct") or 0.0), 4),
                            }
                        )

    leaderboard: list[dict[str, object]] = []
    for bucket in buckets.values():
        filled = int(bucket["filled"])
        if filled == 0:
            continue
        wins = int(bucket["wins"])
        stopped = int(bucket["stopped"])
        wins_count = max(1, int(bucket["winsCount"]))
        stops_count = max(1, int(bucket["stopsCount"]))
        win_context = {key: round(float(bucket["winContext"][key]) / wins_count, 4) for key in TRANSITION_CONTEXT_KEYS}
        stop_context = {key: round(float(bucket["stopContext"][key]) / stops_count, 4) for key in TRANSITION_CONTEXT_KEYS}
        context_edge = {key: round(win_context[key] - stop_context[key], 4) for key in TRANSITION_CONTEXT_KEYS}
        leaderboard.append(
            {
                "transitionType": bucket["transitionType"],
                "setup": bucket["setup"],
                "count": int(bucket["count"]),
                "filled": filled,
                "wins": wins,
                "winRate": round(wins / filled * 100.0, 2),
                "winRateCi95": wilson_ci95(wins, filled),
                "avgNetR": round(float(bucket["sumNetR"]) / filled, 4),
                "cleanRuns": int(bucket["cleanRuns"]),
                "reclaimedRuns": int(bucket["reclaimedRuns"]),
                "stopped": stopped,
                "timedOut": int(bucket["timedOut"]),
                "flipRunsAfterStop": int(bucket["flipRunsAfterStop"]),
                "flipRateAfterStop": round(int(bucket["flipRunsAfterStop"]) / max(1, stopped) * 100.0, 2),
                "avgRiskPct": round(float(bucket["sumRiskPct"]) / filled, 4),
                "avgFirstScore": round(float(bucket["sumFirstScore"]) / max(1, int(bucket["count"])), 2),
                "avgSecondScore": round(float(bucket["sumSecondScore"]) / max(1, int(bucket["count"])), 2),
                "winContext": win_context,
                "stopContext": stop_context,
                "contextEdge": context_edge,
                "records": bucket["records"],
                "samples": bucket["samples"],
            }
        )
    leaderboard.sort(
        key=lambda item: (
            item["winRate"],
            item["filled"],
            item["reclaimedRuns"] + item["cleanRuns"],
            -item["avgRiskPct"],
        ),
        reverse=True,
    )
    return leaderboard


def summarize_transition_families(
    transition_leaderboard: list[dict[str, object]],
    min_filled: int = 3,
) -> list[dict[str, object]]:
    best_by_family: dict[str, dict[str, object]] = {}
    for item in transition_leaderboard:
        filled = int(item["filled"])
        if filled < min_filled:
            continue
        family = str(item["transitionType"])
        current = best_by_family.get(family)
        if current is None:
            best_by_family[family] = item
            continue
        current_score = (
            float(current["winRate"]),
            int(current["filled"]),
            int(current["cleanRuns"]) + int(current["reclaimedRuns"]),
            -float(current["avgRiskPct"]),
        )
        candidate_score = (
            float(item["winRate"]),
            int(item["filled"]),
            int(item["cleanRuns"]) + int(item["reclaimedRuns"]),
            -float(item["avgRiskPct"]),
        )
        if candidate_score > current_score:
            best_by_family[family] = item

    summary: list[dict[str, object]] = []
    for family, item in best_by_family.items():
        summary.append(
            {
                "transitionType": family,
                "bestSetup": item["setup"],
                "filled": item["filled"],
                "wins": item["wins"],
                "winRate": item["winRate"],
                "winRateCi95": item.get("winRateCi95"),
                "avgNetR": item.get("avgNetR"),
                "cleanRuns": item["cleanRuns"],
                "reclaimedRuns": item["reclaimedRuns"],
                "stopped": item["stopped"],
                "timedOut": item["timedOut"],
                "flipRunsAfterStop": item["flipRunsAfterStop"],
                "flipRateAfterStop": item["flipRateAfterStop"],
                "avgRiskPct": item["avgRiskPct"],
                "avgFirstScore": item["avgFirstScore"],
                "avgSecondScore": item["avgSecondScore"],
                "winContext": item["winContext"],
                "stopContext": item["stopContext"],
                "contextEdge": item["contextEdge"],
                "records": item.get("records", []),
                "samples": item["samples"],
            }
        )
    summary.sort(
        key=lambda item: (
            float(item["winRate"]),
            int(item["filled"]),
            int(item["cleanRuns"]) + int(item["reclaimedRuns"]),
            -float(item["avgRiskPct"]),
        ),
        reverse=True,
    )
    return summary


def summarize_transition_context_edges(
    transition_families: list[dict[str, object]],
) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    for item in transition_families:
        edge = dict(item.get("contextEdge") or {})
        summary.append(
            {
                "transitionType": item["transitionType"],
                "bestSetup": item["bestSetup"],
                "filled": item["filled"],
                "winRate": item["winRate"],
                "topPositiveEdges": sorted(edge.items(), key=lambda kv: kv[1], reverse=True)[:5],
                "topNegativeEdges": sorted(edge.items(), key=lambda kv: kv[1])[:5],
                "winContext": item.get("winContext", {}),
                "stopContext": item.get("stopContext", {}),
            }
        )
    summary.sort(key=lambda item: (item["winRate"], item["filled"]), reverse=True)
    return summary


def summarize_reclaim_threshold_sweeps(
    transition_families: list[dict[str, object]],
    train_until_ts: int | None = None,
) -> list[dict[str, object]]:
    target_families = {
        "fakeout_to_reclaim:short->long": "reclaim_quality_long",
        "fakeout_to_reclaim:long->short": "reclaim_quality_short",
        "failed_fakeout_flip:short->long": "reclaim_quality_long",
        "failed_fakeout_flip:long->short": "reclaim_quality_short",
    }
    transition_map = {str(item["transitionType"]): item for item in transition_families}
    summary: list[dict[str, object]] = []
    for family, quality_key in target_families.items():
        item = transition_map.get(family)
        if not item:
            continue
        records = list(item.get("records") or [])
        if train_until_ts is not None:
            records = [record for record in records if int(record["timestamp"]) <= train_until_ts]
        if len(records) < 4:
            continue
        baseline_filled = len(records)
        baseline_wins = sum(1 for record in records if record["result"] in ("clean_run", "reclaimed_run"))
        baseline_rate = round(baseline_wins / max(1, baseline_filled) * 100.0, 2)

        edge = dict(item.get("contextEdge") or {})
        metric_prefs: list[tuple[str, str]] = []
        for metric, value in sorted(edge.items(), key=lambda kv: kv[1], reverse=True)[:3]:
            metric_prefs.append((metric, "min"))
        for metric, value in sorted(edge.items(), key=lambda kv: kv[1])[:3]:
            metric_prefs.append((metric, "max"))
        if quality_key not in {metric for metric, _ in metric_prefs}:
            metric_prefs.insert(0, (quality_key, "min"))

        seen: set[tuple[str, str]] = set()
        sweeps: list[dict[str, object]] = []
        for metric, mode in metric_prefs:
            if (metric, mode) in seen:
                continue
            seen.add((metric, mode))
            values = [to_num(record.get(metric)) for record in records]
            thresholds = unique_quantile_thresholds(values)
            best: dict[str, object] | None = None
            for threshold in thresholds:
                if mode == "min":
                    subset = [record for record in records if to_num(record.get(metric)) >= threshold]
                else:
                    subset = [record for record in records if to_num(record.get(metric)) <= threshold]
                filled = len(subset)
                if filled < 4:
                    continue
                wins = sum(1 for record in subset if record["result"] in ("clean_run", "reclaimed_run"))
                win_rate = wins / max(1, filled) * 100.0
                if win_rate < baseline_rate or wins == 0:
                    continue
                candidate = {
                    "metric": metric,
                    "mode": mode,
                    "threshold": round(float(threshold), 4),
                    "filled": filled,
                    "wins": wins,
                    "winRate": round(win_rate, 2),
                    "winRateCi95": wilson_ci95(wins, filled),
                    "deltaVsBaseline": round(win_rate - baseline_rate, 2),
                }
                if best is None:
                    best = candidate
                    continue
                best_score = (float(best["winRate"]), int(best["filled"]), int(best["wins"]))
                candidate_score = (float(candidate["winRate"]), int(candidate["filled"]), int(candidate["wins"]))
                if candidate_score > best_score:
                    best = candidate
            if best:
                sweeps.append(best)

        sweeps.sort(key=lambda entry: (float(entry["winRate"]), int(entry["filled"]), int(entry["wins"])), reverse=True)
        summary.append(
            {
                "transitionType": family,
                "bestSetup": item["bestSetup"],
                "baselineFilled": baseline_filled,
                "baselineWins": baseline_wins,
                "baselineWinRate": baseline_rate,
                "baselineWinRateCi95": wilson_ci95(baseline_wins, baseline_filled),
                "sweeps": sweeps[:6],
            }
        )
    summary.sort(key=lambda item: (float(item["baselineWinRate"]), int(item["baselineFilled"])), reverse=True)
    return summary


def summarize_robust_transition_gates(
    transition_families: list[dict[str, object]],
    train_until_ts: int | None = None,
) -> list[dict[str, object]]:
    target_families = (
        "fakeout_to_reclaim:short->long",
        "fakeout_to_reclaim:long->short",
        "fakeout_to_breaker:short",
        "fakeout_to_breaker:long",
    )
    transition_map = {str(item["transitionType"]): item for item in transition_families}
    summary: list[dict[str, object]] = []
    for family in target_families:
        item = transition_map.get(family)
        if not item:
            continue
        records = list(item.get("records") or [])
        if train_until_ts is not None:
            records = [record for record in records if int(record["timestamp"]) <= train_until_ts]
        if len(records) < 16:
            continue

        buckets: dict[str, list[dict[str, object]]] = collections.defaultdict(list)
        for record in records:
            buckets[bucket_month(int(record["timestamp"]))].append(record)
        active_buckets = [bucket for bucket in buckets.values() if len(bucket) >= 6]
        if len(active_buckets) < 3:
            continue

        context_edge = dict(item.get("contextEdge") or {})
        metric_candidates: list[tuple[str, str]] = []
        for metric, value in sorted(context_edge.items(), key=lambda kv: kv[1], reverse=True)[:4]:
            metric_candidates.append((metric, "min"))
        for metric, value in sorted(context_edge.items(), key=lambda kv: kv[1])[:4]:
            metric_candidates.append((metric, "max"))

        seen: set[tuple[str, str]] = set()
        gate_candidates: list[dict[str, object]] = []
        for metric, mode in metric_candidates:
            if (metric, mode) in seen:
                continue
            seen.add((metric, mode))
            all_values = [to_num(record.get(metric)) for record in records]
            thresholds = unique_quantile_thresholds(all_values)
            for threshold in thresholds:
                per_bucket: list[dict[str, object]] = []
                total_kept = 0
                total_records = 0
                total_kept_wins = 0
                for bucket in active_buckets:
                    base_filled = len(bucket)
                    base_wins = sum(1 for record in bucket if record["result"] in ("clean_run", "reclaimed_run"))
                    base_rate = base_wins / max(1, base_filled) * 100.0
                    if mode == "min":
                        subset = [record for record in bucket if to_num(record.get(metric)) >= threshold]
                    else:
                        subset = [record for record in bucket if to_num(record.get(metric)) <= threshold]
                    kept = len(subset)
                    if kept < 4:
                        continue
                    wins = sum(1 for record in subset if record["result"] in ("clean_run", "reclaimed_run"))
                    kept_rate = wins / max(1, kept) * 100.0
                    per_bucket.append(
                        {
                            "baseRate": base_rate,
                            "keptRate": kept_rate,
                            "uplift": kept_rate - base_rate,
                            "baseFilled": base_filled,
                            "kept": kept,
                        }
                    )
                    total_records += base_filled
                    total_kept += kept
                    total_kept_wins += wins
                if len(per_bucket) < 3:
                    continue
                mean_uplift = sum(entry["uplift"] for entry in per_bucket) / len(per_bucket)
                positive_share = sum(1 for entry in per_bucket if entry["uplift"] > 0.0) / len(per_bucket)
                overall_win_rate = total_kept_wins / max(1, total_kept) * 100.0
                baseline_total_wins = sum(
                    sum(1 for record in bucket if record["result"] in ("clean_run", "reclaimed_run"))
                    for bucket in active_buckets
                )
                baseline_total_filled = sum(len(bucket) for bucket in active_buckets)
                baseline_win_rate = baseline_total_wins / max(1, baseline_total_filled) * 100.0
                keep_rate = total_kept / max(1, total_records) * 100.0
                if mean_uplift <= 0.0 or positive_share < 0.5 or keep_rate < 10.0:
                    continue
                gate_candidates.append(
                    {
                        "transitionType": family,
                        "metric": metric,
                        "mode": mode,
                        "threshold": round(float(threshold), 4),
                        "meanUplift": round(mean_uplift, 2),
                        "positiveShare": round(positive_share * 100.0, 2),
                        "baselineWinRate": round(baseline_win_rate, 2),
                        "baselineWinRateCi95": wilson_ci95(baseline_total_wins, baseline_total_filled),
                        "gatedWinRate": round(overall_win_rate, 2),
                        "gatedWinRateCi95": wilson_ci95(total_kept_wins, total_kept),
                        "keepRate": round(keep_rate, 2),
                        "evaluatedBuckets": len(per_bucket),
                    }
                )

        gate_candidates.sort(
            key=lambda item: (
                float(item["meanUplift"]),
                float(item["positiveShare"]),
                float(item["keepRate"]),
                float(item["gatedWinRate"]),
            ),
            reverse=True,
        )
        if gate_candidates:
            summary.append(
                {
                    "transitionType": family,
                    "bestGates": gate_candidates[:6],
                }
            )
    return summary


def build_transition_gate_profiles(
    robust_transition_gates: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    profiles: dict[str, dict[str, object]] = {}
    for item in robust_transition_gates:
        gates = list(item.get("bestGates") or [])
        if not gates:
            continue
        best = gates[0]
        if (
            float(best["meanUplift"]) >= 2.0
            and float(best["positiveShare"]) >= 60.0
            and float(best["keepRate"]) >= 15.0
        ):
            profiles[str(item["transitionType"])] = {
                "metric": str(best["metric"]),
                "mode": str(best["mode"]),
                "threshold": float(best["threshold"]),
                "meanUplift": float(best["meanUplift"]),
                "positiveShare": float(best["positiveShare"]),
                "keepRate": float(best["keepRate"]),
            }
    return profiles


def split_records_by_month(records: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    buckets: dict[str, list[dict[str, object]]] = collections.defaultdict(list)
    for record in records:
        buckets[bucket_month(int(record["timestamp"]))].append(record)
    return dict(sorted(buckets.items()))


def summarize_robust_gates_for_records(
    transition_type: str,
    records: list[dict[str, object]],
    metric_candidates: list[tuple[str, str]] | None = None,
    min_bucket_records: int = 4,
) -> list[dict[str, object]]:
    bucketed = split_records_by_month(records)
    active_buckets = [bucket for bucket in bucketed.values() if len(bucket) >= min_bucket_records]
    if len(active_buckets) < 3:
        return []

    gate_candidates: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    candidates = metric_candidates or [(metric, mode) for metric in TRANSITION_CONTEXT_KEYS for mode in ("min", "max")]
    for metric, mode in candidates:
        if (metric, mode) in seen:
            continue
        seen.add((metric, mode))
        all_values = [to_num(record.get(metric)) for record in records]
        thresholds = unique_quantile_thresholds(all_values)
        for threshold in thresholds:
            per_bucket: list[dict[str, float]] = []
            total_kept = 0
            total_records = 0
            total_kept_wins = 0
            total_bucket_wins = 0
            for bucket in active_buckets:
                base_filled = len(bucket)
                base_wins = sum(1 for record in bucket if record["result"] in ("clean_run", "reclaimed_run"))
                base_rate = base_wins / max(1, base_filled) * 100.0
                if mode == "min":
                    subset = [record for record in bucket if to_num(record.get(metric)) >= threshold]
                else:
                    subset = [record for record in bucket if to_num(record.get(metric)) <= threshold]
                kept = len(subset)
                if kept < min_bucket_records:
                    continue
                wins = sum(1 for record in subset if record["result"] in ("clean_run", "reclaimed_run"))
                kept_rate = wins / max(1, kept) * 100.0
                per_bucket.append(
                    {
                        "baseRate": base_rate,
                        "keptRate": kept_rate,
                        "uplift": kept_rate - base_rate,
                    }
                )
                total_records += base_filled
                total_kept += kept
                total_kept_wins += wins
                total_bucket_wins += base_wins
            if len(per_bucket) < 3:
                continue
            mean_uplift = sum(entry["uplift"] for entry in per_bucket) / len(per_bucket)
            positive_share = sum(1 for entry in per_bucket if entry["uplift"] > 0.0) / len(per_bucket)
            keep_rate = total_kept / max(1, total_records) * 100.0
            baseline_win_rate = total_bucket_wins / max(1, total_records) * 100.0
            gated_win_rate = total_kept_wins / max(1, total_kept) * 100.0
            if mean_uplift <= 0.0 or positive_share < 0.5 or keep_rate < 10.0:
                continue
            gate_candidates.append(
                {
                    "transitionType": transition_type,
                    "metric": metric,
                    "mode": mode,
                    "threshold": round(float(threshold), 4),
                    "meanUplift": round(mean_uplift, 2),
                    "positiveShare": round(positive_share * 100.0, 2),
                    "baselineWinRate": round(baseline_win_rate, 2),
                    "gatedWinRate": round(gated_win_rate, 2),
                    "keepRate": round(keep_rate, 2),
                    "evaluatedBuckets": len(per_bucket),
                }
            )
    gate_candidates.sort(
        key=lambda item: (
            float(item["meanUplift"]),
            float(item["positiveShare"]),
            float(item["keepRate"]),
            float(item["gatedWinRate"]),
        ),
        reverse=True,
    )
    return gate_candidates


def choose_transition_profile(
    transition_type: str,
    records: list[dict[str, object]],
    metric_candidates: list[tuple[str, str]] | None = None,
) -> dict[str, object] | None:
    candidates = summarize_robust_gates_for_records(transition_type, records, metric_candidates=metric_candidates)
    if not candidates:
        return None
    best = candidates[0]
    if (
        float(best["meanUplift"]) >= 2.0
        and float(best["positiveShare"]) >= 60.0
        and float(best["keepRate"]) >= 15.0
    ):
        return {
            "metric": str(best["metric"]),
            "mode": str(best["mode"]),
            "threshold": float(best["threshold"]),
            "meanUplift": float(best["meanUplift"]),
            "positiveShare": float(best["positiveShare"]),
            "keepRate": float(best["keepRate"]),
        }
    return None


def transition_record_passes(record: dict[str, object], profile: dict[str, object]) -> bool:
    metric = str(profile["metric"])
    mode = str(profile["mode"])
    threshold = float(profile["threshold"])
    value = to_num(record.get(metric))
    if mode == "min":
        return value >= threshold
    if mode == "max":
        return value <= threshold
    return True


def choose_best_setup_from_train(
    transition_leaderboard: list[dict[str, object]],
    transition_type: str,
    train_bucket_keys: set[str],
    min_filled: int = 20,
) -> dict[str, object] | None:
    best: dict[str, object] | None = None
    best_score: tuple[float, int, int, float] | None = None
    for item in transition_leaderboard:
        if str(item["transitionType"]) != transition_type:
            continue
        train_records = [
            record
            for record in (item.get("records") or [])
            if bucket_month(int(record["timestamp"])) in train_bucket_keys
        ]
        filled = len(train_records)
        if filled < min_filled:
            continue
        wins = sum(1 for record in train_records if record["result"] in ("clean_run", "reclaimed_run"))
        clean = sum(1 for record in train_records if record["result"] == "clean_run")
        reclaimed = sum(1 for record in train_records if record["result"] == "reclaimed_run")
        avg_risk = sum(float(record["riskPct"]) for record in train_records) / max(1, filled)
        score = (wins / max(1, filled) * 100.0, filled, clean + reclaimed, -avg_risk)
        if best_score is None or score > best_score:
            best_score = score
            best = {
                "transitionType": transition_type,
                "setup": item["setup"],
                "records": train_records,
            }
    return best


def summarize_walk_forward_validation(
    transition_leaderboard: list[dict[str, object]],
) -> list[dict[str, object]]:
    target_families = (
        "fakeout_to_reclaim:short->long",
        "fakeout_to_reclaim:long->short",
        "fakeout_to_breaker:short",
        "fakeout_to_breaker:long",
    )
    full_records_by_family: dict[str, list[dict[str, object]]] = collections.defaultdict(list)
    for item in transition_leaderboard:
        full_records_by_family[str(item["transitionType"])].extend(item.get("records") or [])

    summaries: list[dict[str, object]] = []
    for family in target_families:
        all_records = sorted(full_records_by_family.get(family) or [], key=lambda record: int(record["timestamp"]))
        if not all_records:
            continue
        family_buckets = split_records_by_month(all_records)
        bucket_keys = list(family_buckets.keys())
        if len(bucket_keys) < 6:
            continue
        bucket_results: list[dict[str, object]] = []
        train_setup_names: collections.Counter[str] = collections.Counter()
        train_profiles: list[dict[str, object]] = []
        for idx in range(4, len(bucket_keys)):
            train_bucket_keys = set(bucket_keys[:idx])
            test_key = bucket_keys[idx]
            chosen_setup = choose_best_setup_from_train(transition_leaderboard, family, train_bucket_keys)
            if not chosen_setup:
                continue
            # Gate metric shortlist must come from train records only — using the
            # full-sample robust gates here would leak OOS information.
            train_records = list(chosen_setup["records"])
            train_gates = summarize_robust_gates_for_records(family, train_records)
            metric_candidates = [(str(gate["metric"]), str(gate["mode"])) for gate in train_gates[:4]] or None
            profile = choose_transition_profile(
                family,
                train_records,
                metric_candidates=metric_candidates,
            )
            if not profile:
                continue
            test_setup_item = next(
                (
                    item
                    for item in transition_leaderboard
                    if str(item["transitionType"]) == family and str(item["setup"]) == str(chosen_setup["setup"])
                ),
                None,
            )
            if test_setup_item is None:
                continue
            test_records = [
                record
                for record in (test_setup_item.get("records") or [])
                if bucket_month(int(record["timestamp"])) == test_key
            ]
            if len(test_records) < 4:
                continue
            baseline_wins = sum(1 for record in test_records if record["result"] in ("clean_run", "reclaimed_run"))
            baseline_rate = baseline_wins / max(1, len(test_records)) * 100.0
            kept_records = [record for record in test_records if transition_record_passes(record, profile)]
            if len(kept_records) < 2:
                continue
            kept_wins = sum(1 for record in kept_records if record["result"] in ("clean_run", "reclaimed_run"))
            kept_rate = kept_wins / max(1, len(kept_records)) * 100.0
            baseline_net_r = sum(float(record.get("netR") or 0.0) for record in test_records) / max(1, len(test_records))
            gated_net_r = sum(float(record.get("netR") or 0.0) for record in kept_records) / max(1, len(kept_records))
            train_setup_names[str(chosen_setup["setup"])] += 1
            train_profiles.append(profile)
            bucket_results.append(
                {
                    "testBucket": test_key,
                    "setup": str(chosen_setup["setup"]),
                    "baselineFilled": len(test_records),
                    "baselineWins": baseline_wins,
                    "baselineWinRate": round(baseline_rate, 2),
                    "baselineWinRateCi95": wilson_ci95(baseline_wins, len(test_records)),
                    "baselineAvgNetR": round(baseline_net_r, 4),
                    "gatedFilled": len(kept_records),
                    "gatedWins": kept_wins,
                    "gatedWinRate": round(kept_rate, 2),
                    "gatedWinRateCi95": wilson_ci95(kept_wins, len(kept_records)),
                    "gatedAvgNetR": round(gated_net_r, 4),
                    "uplift": round(kept_rate - baseline_rate, 2),
                    "keepRate": round(len(kept_records) / max(1, len(test_records)) * 100.0, 2),
                    "profile": profile,
                }
            )
        if not bucket_results:
            continue
        baseline_filled = sum(item["baselineFilled"] for item in bucket_results)
        baseline_wins = sum(item["baselineWins"] for item in bucket_results)
        gated_filled = sum(item["gatedFilled"] for item in bucket_results)
        gated_wins = sum(item["gatedWins"] for item in bucket_results)
        positive_buckets = sum(1 for item in bucket_results if item["uplift"] > 0.0)
        summaries.append(
            {
                "transitionType": family,
                "bucketsEvaluated": len(bucket_results),
                "baselineFilled": baseline_filled,
                "baselineWins": baseline_wins,
                "baselineWinRate": round(baseline_wins / max(1, baseline_filled) * 100.0, 2),
                "baselineWinRateCi95": wilson_ci95(baseline_wins, baseline_filled),
                "gatedFilled": gated_filled,
                "gatedWins": gated_wins,
                "gatedWinRate": round(gated_wins / max(1, gated_filled) * 100.0, 2),
                "gatedWinRateCi95": wilson_ci95(gated_wins, gated_filled),
                "avgBaselineNetR": round(
                    sum(float(item["baselineAvgNetR"]) for item in bucket_results) / len(bucket_results), 4
                ),
                "avgGatedNetR": round(
                    sum(float(item["gatedAvgNetR"]) for item in bucket_results) / len(bucket_results), 4
                ),
                "avgUplift": round(sum(item["uplift"] for item in bucket_results) / len(bucket_results), 2),
                "positiveBuckets": positive_buckets,
                "positiveBucketShare": round(positive_buckets / len(bucket_results) * 100.0, 2),
                "avgKeepRate": round(sum(item["keepRate"] for item in bucket_results) / len(bucket_results), 2),
                "mostCommonSetup": train_setup_names.most_common(1)[0][0],
                "recentProfiles": train_profiles[-3:],
                "bucketResults": bucket_results[-8:],
            }
        )
    summaries.sort(
        key=lambda item: (
            float(item["gatedWinRate"]),
            float(item["avgUplift"]),
            int(item["gatedFilled"]),
        ),
        reverse=True,
    )
    return summaries


def summarize_family_contexts(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    buckets: dict[str, dict[str, float]] = {}
    for candidate in candidates:
        family_direction = f"{candidate['signal_family']}:{candidate['direction']}"
        bucket = buckets.setdefault(
            family_direction,
            {
                "count": 0.0,
                "score": 0.0,
                "direction_margin": 0.0,
                "regime_margin": 0.0,
                "micro_margin": 0.0,
                "lifecycle_margin": 0.0,
                "lifecycle_pressure": 0.0,
                "lifecycle_maturity": 0.0,
                "volume_margin": 0.0,
                "spot_margin": 0.0,
                "futures_margin": 0.0,
                "lead_margin": 0.0,
                "whale_margin": 0.0,
                "breakout_force": 0.0,
                "rejection_force": 0.0,
                "compression_force": 0.0,
            },
        )
        bucket["count"] += 1.0
        bucket["score"] += float(candidate["score"])
        bucket["direction_margin"] += float(candidate["aggregate"]["direction_margin"])
        bucket["regime_margin"] += float(candidate["cycle_total"]["regime_bull"]) - float(candidate["cycle_total"]["regime_bear"])
        bucket["micro_margin"] += float(candidate["cycle_total"]["micro_bull"]) - float(candidate["cycle_total"]["micro_bear"])
        bucket["lifecycle_margin"] += float(candidate["cycle_total"]["lifecycle_bull"]) - float(candidate["cycle_total"]["lifecycle_bear"])
        bucket["lifecycle_pressure"] += float(candidate["cycle_total"]["lifecycle_pressure"])
        bucket["lifecycle_maturity"] += float(candidate["cycle_total"]["lifecycle_maturity"])
        bucket["volume_margin"] += float(candidate["volume_total"]["bull"]) - float(candidate["volume_total"]["bear"])
        bucket["spot_margin"] += float(candidate["volume_total"]["spot_bull"]) - float(candidate["volume_total"]["spot_bear"])
        bucket["futures_margin"] += float(candidate["volume_total"]["futures_bull"]) - float(candidate["volume_total"]["futures_bear"])
        bucket["lead_margin"] += float(candidate["volume_total"]["lead_bull"]) - float(candidate["volume_total"]["lead_bear"])
        bucket["whale_margin"] += float(candidate["volume_total"]["whale_bull"]) - float(candidate["volume_total"]["whale_bear"])
        bucket["breakout_force"] += float(candidate["volume_total"]["breakout"])
        bucket["rejection_force"] += float(candidate["volume_total"]["rejection"]) + float(candidate["volume_total"]["exhaustion"])
        bucket["compression_force"] += float(candidate["aggregate"]["compression"]) + float(candidate["volume_total"]["compression"])

    summary: list[dict[str, object]] = []
    for family_direction, bucket in buckets.items():
        count = max(1.0, bucket["count"])
        summary.append(
            {
                "familyDirection": family_direction,
                "count": int(bucket["count"]),
                "avgScore": round(bucket["score"] / count, 2),
                "avgDirectionMargin": round(bucket["direction_margin"] / count, 2),
                "avgRegimeMargin": round(bucket["regime_margin"] / count, 2),
                "avgMicroMargin": round(bucket["micro_margin"] / count, 2),
                "avgLifecycleMargin": round(bucket["lifecycle_margin"] / count, 2),
                "avgLifecyclePressure": round(bucket["lifecycle_pressure"] / count, 2),
                "avgLifecycleMaturity": round(bucket["lifecycle_maturity"] / count, 2),
                "avgVolumeMargin": round(bucket["volume_margin"] / count, 2),
                "avgSpotMargin": round(bucket["spot_margin"] / count, 2),
                "avgFuturesMargin": round(bucket["futures_margin"] / count, 2),
                "avgLeadMargin": round(bucket["lead_margin"] / count, 2),
                "avgWhaleMargin": round(bucket["whale_margin"] / count, 2),
                "avgBreakoutForce": round(bucket["breakout_force"] / count, 2),
                "avgRejectionForce": round(bucket["rejection_force"] / count, 2),
                "avgCompressionForce": round(bucket["compression_force"] / count, 2),
            }
        )
    summary.sort(key=lambda item: (item["avgScore"], item["count"]), reverse=True)
    return summary


def run_research(
    db_path: str,
    signal_threshold: float,
    cooldown_bars: int,
    since_ts: int | None = None,
    until_ts: int | None = None,
    use_warehouse: bool = False,
    fee_bps: float = 5.0,
    slippage_bps: float = 5.0,
    train_fraction: float = 0.65,
) -> dict[str, object]:
    conn = connect_research_backend(db_path, use_warehouse=use_warehouse)
    thresholds = {}
    if use_warehouse or db_path.endswith(".duckdb"):
        joined_rows = build_joined_warehouse_rows(conn, since_ts=since_ts, until_ts=until_ts)
        rows_1m, higher_rows = split_joined_mtf_rows(joined_rows)
    else:
        higher_rows = {
            tf: load_timeframe_rows(conn, tf, since_ts=since_ts, until_ts=until_ts)
            for tf in HIGHER_TFS
        }
        if since_ts is None and until_ts is None:
            rows_1m = query_rows(conn, f"SELECT {FEATURE_COLUMNS} FROM candles_1m ORDER BY timestamp")
        else:
            clauses = []
            params: list[int] = []
            if since_ts is not None:
                clauses.append("timestamp >= ?")
                params.append(since_ts)
            if until_ts is not None:
                clauses.append("timestamp <= ?")
                params.append(until_ts)
            where = " AND ".join(clauses)
            rows_1m = query_rows(
                conn,
                f"SELECT {FEATURE_COLUMNS} FROM candles_1m WHERE {where} ORDER BY timestamp",
                tuple(params),
            )

    threshold_rows = {
        tf: load_timeframe_rows(conn, tf, since_ts=since_ts, until_ts=until_ts, columns=THRESHOLD_COLUMNS)
        for tf in RESEARCH_TFS
    }
    # Percentile thresholds are estimated on the train prefix only and then
    # frozen, so scores in the later OOS region use no future information.
    train_cutoff_ts: int | None = None
    if rows_1m:
        train_fraction = min(1.0, max(0.0, train_fraction))
        cutoff_index = min(len(rows_1m) - 1, int(len(rows_1m) * train_fraction))
        train_cutoff_ts = int(rows_1m[cutoff_index]["timestamp"])
    train_threshold_rows = {
        tf: [
            row
            for row in threshold_rows[tf]
            if train_cutoff_ts is None or int(row["timestamp"]) <= train_cutoff_ts
        ]
        for tf in RESEARCH_TFS
    }
    thresholds["1m"] = build_thresholds(train_threshold_rows["1m"])
    for tf in HIGHER_TFS:
        thresholds[tf] = build_thresholds(train_threshold_rows[tf])

    if use_warehouse or db_path.endswith(".duckdb"):
        cycle_rows = {
            "1m": [{"timestamp": row["timestamp"], "close": row["close"]} for row in rows_1m],
            **{
                tf: [{"timestamp": row["timestamp"], "close": row["close"]} for row in higher_rows[tf]]
                for tf in HIGHER_TFS
            },
        }
    else:
        cycle_rows = {
            tf: load_timeframe_rows(conn, tf, since_ts=None, until_ts=until_ts, columns=CYCLE_TS_COLUMNS)
            for tf in RESEARCH_TFS
        }
    cycle_cache = build_cycle_metric_cache(conn, cycle_rows)
    cycle_state_cache = build_cycle_state_score_cache(cycle_cache)
    snapshot_score_cache = build_snapshot_score_cache({"1m": rows_1m, **higher_rows}, thresholds)
    volume_score_cache = build_volume_breaker_score_cache({"1m": rows_1m, **higher_rows})

    cursors = {tf: TfCursor(rows=rows, tf_ms=TF_MS[tf]) for tf, rows in higher_rows.items()} if not (use_warehouse or db_path.endswith(".duckdb")) else None

    recent_bull: collections.deque[float] = collections.deque(maxlen=30)
    recent_bear: collections.deque[float] = collections.deque(maxlen=30)
    last_signal_bar: dict[tuple[str, str], int] = {}
    candidates: list[dict[str, object]] = []
    family_signal_counts: collections.Counter[str] = collections.Counter()

    for bar_index, row in enumerate(rows_1m):
        ts = int(row["timestamp"])
        if cursors is None:
            mtf_timestamps = mtf_timestamps_from_joined_row(joined_rows[bar_index])
        else:
            mtf_rows = {tf: cursor.advance_to(ts) for tf, cursor in cursors.items()}
            mtf_timestamps = mtf_timestamps_from_rows(mtf_rows)
        aggregate = aggregate_candidate_context(ts, mtf_timestamps, snapshot_score_cache)
        cycle_total = aggregate_cycle_context(cycle_state_cache, ts, mtf_timestamps)
        volume_total = aggregate_volume_breaker_context(ts, mtf_timestamps, volume_score_cache)
        signal_family, direction = determine_signal_family(
            row=row,
            aggregate=aggregate,
            recent_bull_max=max(recent_bull) if recent_bull else 0.0,
            recent_bear_max=max(recent_bear) if recent_bear else 0.0,
            cycle_total=cycle_total,
            volume_total=volume_total,
        )
        reclaim_quality_long = reclaim_quality_score("long", aggregate, cycle_total, volume_total)
        reclaim_quality_short = reclaim_quality_score("short", aggregate, cycle_total, volume_total)
        recent_bull.append(float(aggregate["bull"]))
        recent_bear.append(float(aggregate["bear"]))
        if not signal_family or not direction:
            continue

        score = (
            max(float(aggregate["bull"]), float(aggregate["bear"]))
            + float(aggregate["compression"])
            + float(aggregate["event"])
            + max(float(cycle_total["regime_bull"]), float(cycle_total["regime_bear"]))
            + max(float(cycle_total["micro_bull"]), float(cycle_total["micro_bear"]))
            + max(float(volume_total["bull"]), float(volume_total["bear"]))
            + float(volume_total["breakout"])
        )
        if score < signal_threshold:
            continue

        key = (signal_family, direction)
        previous_bar = last_signal_bar.get(key)
        if previous_bar is not None and bar_index - previous_bar < cooldown_bars:
            continue
        last_signal_bar[key] = bar_index

        candidates.append(
            {
                "bar_index": bar_index,
                "timestamp": ts,
                "open": to_num(row["open"]),
                "high": to_num(row["high"]),
                "low": to_num(row["low"]),
                "close": to_num(row["close"]),
                "signal_family": signal_family,
                "direction": direction,
                "score": score,
                "aggregate": aggregate,
                "cycle_total": cycle_total,
                "volume_total": volume_total,
                "reason": candidate_reason(signal_family, direction, aggregate, cycle_total, volume_total),
                "m1_delta_pct": to_num(row["delta_pct"]),
                "m1_bos_bull": int(to_num(row["bos_bull"])),
                "m1_bos_bear": int(to_num(row["bos_bear"])),
                "m1_choch": int(to_num(row["choch"])),
                "m1_sw_bullish": int(to_num(row["sw_bullish"])),
                "m1_bos_body": int(to_num(row["bos_body"])),
                "m1_bos_wick": int(to_num(row["bos_wick"])),
                "m1_is_seeker_div_hs": int(to_num(row["is_seeker_div_hs"])),
                "m1_is_seeker_div_ls": int(to_num(row["is_seeker_div_ls"])),
                "m1_is_seeker_kill": int(to_num(row["is_seeker_kill"])),
                "m1_killed_seekers_count": int(to_num(row["killed_seekers_count"])),
                "m1_vol_vs_ma": to_num(row["vol_vs_ma"], 1.0),
                "m1_cluster_range_atr": to_num(row["cluster_range_atr"]),
                "m1_futures_minus_spot_volume": to_num(row["futures_minus_spot_volume"]),
                "m1_dist_swing_high": to_num(row["dist_swing_high"]),
                "m1_dist_swing_low": to_num(row["dist_swing_low"]),
                "lifecycle_margin": float(cycle_total["lifecycle_bull"]) - float(cycle_total["lifecycle_bear"]),
                "lifecycle_pressure": float(cycle_total["lifecycle_pressure"]),
                "lifecycle_maturity": float(cycle_total["lifecycle_maturity"]),
                "volume_margin": float(volume_total["bull"]) - float(volume_total["bear"]),
                "breakout_force": float(volume_total["breakout"]),
                "rejection_force": float(volume_total["rejection"]) + float(volume_total["exhaustion"]),
                "compression_force": float(aggregate["compression"]) + float(volume_total["compression"]),
                "reclaim_quality_long": float(reclaim_quality_long),
                "reclaim_quality_short": float(reclaim_quality_short),
            }
        )
        family_signal_counts[f"{signal_family}:{direction}"] += 1

    results: dict[str, dict[str, dict[str, object]]] = {}
    sample_cases: list[dict[str, object]] = []
    today_cases: list[dict[str, object]] = []
    today_signals: dict[int, dict[str, object]] = {}
    best_signals: dict[int, dict[str, object]] = {}

    for signal in candidates:
        family = str(signal["signal_family"])
        direction = str(signal["direction"])
        family_bucket = results.setdefault(f"{family}:{direction}", {})
        for entry_model in ENTRY_MODELS:
            for stop_model in STOP_MODELS:
                for target_model in TARGET_MODELS:
                    bucket_key = f"{entry_model}|{stop_model}|{target_model}"
                    bucket = family_bucket.setdefault(
                        bucket_key,
                        {
                            "trades": 0,
                            "filled": 0,
                            "wins": 0,
                            "clean_runs": 0,
                            "reclaimed_runs": 0,
                            "stopped": 0,
                            "timed_out": 0,
                            "flip_runs_after_stop": 0,
                            "sum_risk_pct": 0.0,
                            "sum_net_r": 0.0,
                            "sum_score": 0.0,
                            "sample": [],
                        },
                    )
                    bucket["trades"] += 1
                    bucket["sum_score"] += float(signal["score"])
                    outcome = evaluate_trade_on_rows(
                        rows_1m=rows_1m,
                        signal=signal,
                        entry_model=entry_model,
                        stop_model=stop_model,
                        target_model=target_model,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                    )
                    if not outcome.get("filled"):
                        continue
                    bucket["filled"] += 1
                    bucket["sum_risk_pct"] += float(outcome.get("risk_pct") or 0.0)
                    bucket["sum_net_r"] += float(outcome.get("net_r") or 0.0)
                    result = str(outcome["result"])
                    if result in ("clean_run", "reclaimed_run"):
                        bucket["wins"] += 1
                    if result == "clean_run":
                        bucket["clean_runs"] += 1
                    elif result == "reclaimed_run":
                        bucket["reclaimed_runs"] += 1
                    elif result == "stopped":
                        bucket["stopped"] += 1
                    else:
                        bucket["timed_out"] += 1
                    if outcome.get("flip_run_after_stop"):
                        bucket["flip_runs_after_stop"] += 1

                    sample_entry = {
                        "timestamp": signal["timestamp"],
                        "time": fmt_ts(int(signal["timestamp"])),
                        "family": family,
                        "direction": direction,
                        "setup": bucket_key,
                        "reason": signal["reason"],
                        "result": result,
                        "risk_pct": round(float(outcome.get("risk_pct") or 0.0), 4),
                        "score": round(float(signal["score"]), 2),
                        "flip_run_after_stop": bool(outcome.get("flip_run_after_stop")),
                    }
                    if len(bucket["sample"]) < 5:
                        bucket["sample"].append(sample_entry)
                        if len(sample_cases) < 16 and result in ("clean_run", "reclaimed_run"):
                            sample_cases.append(sample_entry)
                        if sample_entry["time"].startswith("11.03.26"):
                            today_cases.append(sample_entry)
                            current = today_signals.get(int(signal["timestamp"]))
                            candidate = {
                                "bar_index": int(signal["bar_index"]),
                                "timestamp": int(signal["timestamp"]),
                                "time": fmt_ts(int(signal["timestamp"])),
                                "family": family,
                                "direction": direction,
                                "score": round(float(signal["score"]), 2),
                                "reason": signal["reason"],
                                "bestSetup": bucket_key,
                                "bestResult": result,
                                "risk_pct": round(float(outcome.get("risk_pct") or 0.0), 4),
                                "flip_run_after_stop": bool(outcome.get("flip_run_after_stop")),
                                "direction_margin": round(float(signal["aggregate"]["direction_margin"]), 4),
                                "regime_margin": round(
                                    float(signal["cycle_total"]["regime_bull"]) - float(signal["cycle_total"]["regime_bear"]),
                                    4,
                                ),
                                "micro_margin": round(
                                    float(signal["cycle_total"]["micro_bull"]) - float(signal["cycle_total"]["micro_bear"]),
                                    4,
                                ),
                                "lifecycle_margin": round(
                                    float(signal["cycle_total"]["lifecycle_bull"]) - float(signal["cycle_total"]["lifecycle_bear"]),
                                    4,
                                ),
                                "lifecycle_pressure": round(float(signal["cycle_total"]["lifecycle_pressure"]), 4),
                                "lifecycle_maturity": round(float(signal["cycle_total"]["lifecycle_maturity"]), 4),
                                "volume_margin": round(
                                    float(signal["volume_total"]["bull"]) - float(signal["volume_total"]["bear"]),
                                    4,
                                ),
                                "spot_margin": round(
                                    float(signal["volume_total"]["spot_bull"]) - float(signal["volume_total"]["spot_bear"]),
                                    4,
                                ),
                                "futures_margin": round(
                                    float(signal["volume_total"]["futures_bull"]) - float(signal["volume_total"]["futures_bear"]),
                                    4,
                                ),
                                "lead_margin": round(
                                    float(signal["volume_total"]["lead_bull"]) - float(signal["volume_total"]["lead_bear"]),
                                    4,
                                ),
                                "whale_margin": round(
                                    float(signal["volume_total"]["whale_bull"]) - float(signal["volume_total"]["whale_bear"]),
                                    4,
                                ),
                                "breakout_force": round(float(signal["volume_total"]["breakout"]), 4),
                                "rejection_force": round(
                                    float(signal["volume_total"]["rejection"]) + float(signal["volume_total"]["exhaustion"]),
                                    4,
                                ),
                                "compression_force": round(
                                    float(signal["aggregate"]["compression"]) + float(signal["volume_total"]["compression"]),
                                    4,
                                ),
                                "reclaim_quality_long": round(float(signal["reclaim_quality_long"]), 4),
                                "reclaim_quality_short": round(float(signal["reclaim_quality_short"]), 4),
                                "m1_bos_bull": int(signal["m1_bos_bull"]),
                                "m1_bos_bear": int(signal["m1_bos_bear"]),
                                "m1_choch": int(signal["m1_choch"]),
                                "m1_sw_bullish": int(signal["m1_sw_bullish"]),
                                "m1_bos_body": int(signal["m1_bos_body"]),
                                "m1_bos_wick": int(signal["m1_bos_wick"]),
                                "m1_is_seeker_kill": int(signal["m1_is_seeker_kill"]),
                                "m1_futures_minus_spot_volume": round(float(signal["m1_futures_minus_spot_volume"]), 4),
                            }
                            if current is None or candidate["score"] > current["score"]:
                                today_signals[int(signal["timestamp"])] = candidate
                    current_best = best_signals.get(int(signal["timestamp"]))
                    global_candidate = {
                        "bar_index": int(signal["bar_index"]),
                        "timestamp": int(signal["timestamp"]),
                        "time": fmt_ts(int(signal["timestamp"])),
                        "family": family,
                        "direction": direction,
                        "open": signal["open"],
                        "high": signal["high"],
                        "low": signal["low"],
                        "close": signal["close"],
                        "score": round(float(signal["score"]), 2),
                        "reason": signal["reason"],
                        "bestSetup": bucket_key,
                        "bestResult": result,
                        "risk_pct": round(float(outcome.get("risk_pct") or 0.0), 4),
                        "flip_run_after_stop": bool(outcome.get("flip_run_after_stop")),
                        "direction_margin": round(float(signal["aggregate"]["direction_margin"]), 4),
                        "regime_margin": round(
                            float(signal["cycle_total"]["regime_bull"]) - float(signal["cycle_total"]["regime_bear"]),
                            4,
                        ),
                        "micro_margin": round(
                            float(signal["cycle_total"]["micro_bull"]) - float(signal["cycle_total"]["micro_bear"]),
                            4,
                        ),
                        "lifecycle_margin": round(
                            float(signal["cycle_total"]["lifecycle_bull"]) - float(signal["cycle_total"]["lifecycle_bear"]),
                            4,
                        ),
                        "lifecycle_pressure": round(float(signal["cycle_total"]["lifecycle_pressure"]), 4),
                        "lifecycle_maturity": round(float(signal["cycle_total"]["lifecycle_maturity"]), 4),
                        "volume_margin": round(
                            float(signal["volume_total"]["bull"]) - float(signal["volume_total"]["bear"]),
                            4,
                        ),
                        "spot_margin": round(
                            float(signal["volume_total"]["spot_bull"]) - float(signal["volume_total"]["spot_bear"]),
                            4,
                        ),
                        "futures_margin": round(
                            float(signal["volume_total"]["futures_bull"]) - float(signal["volume_total"]["futures_bear"]),
                            4,
                        ),
                        "lead_margin": round(
                            float(signal["volume_total"]["lead_bull"]) - float(signal["volume_total"]["lead_bear"]),
                            4,
                        ),
                        "whale_margin": round(
                            float(signal["volume_total"]["whale_bull"]) - float(signal["volume_total"]["whale_bear"]),
                            4,
                        ),
                        "breakout_force": round(float(signal["volume_total"]["breakout"]), 4),
                        "rejection_force": round(
                            float(signal["volume_total"]["rejection"]) + float(signal["volume_total"]["exhaustion"]),
                            4,
                        ),
                        "compression_force": round(
                            float(signal["aggregate"]["compression"]) + float(signal["volume_total"]["compression"]),
                            4,
                        ),
                        "reclaim_quality_long": round(float(signal["reclaim_quality_long"]), 4),
                        "reclaim_quality_short": round(float(signal["reclaim_quality_short"]), 4),
                        "m1_bos_bull": int(signal["m1_bos_bull"]),
                        "m1_bos_bear": int(signal["m1_bos_bear"]),
                        "m1_choch": int(signal["m1_choch"]),
                        "m1_sw_bullish": int(signal["m1_sw_bullish"]),
                        "m1_bos_body": int(signal["m1_bos_body"]),
                        "m1_bos_wick": int(signal["m1_bos_wick"]),
                        "m1_is_seeker_kill": int(signal["m1_is_seeker_kill"]),
                        "m1_futures_minus_spot_volume": round(float(signal["m1_futures_minus_spot_volume"]), 4),
                    }
                    if (
                        current_best is None
                        or result_rank(str(global_candidate["bestResult"])) > result_rank(str(current_best["bestResult"]))
                        or (
                            result_rank(str(global_candidate["bestResult"])) == result_rank(str(current_best["bestResult"]))
                            and global_candidate["score"] > current_best["score"]
                        )
                    ):
                        best_signals[int(signal["timestamp"])] = global_candidate

    leaderboard: list[dict[str, object]] = []
    for family_direction, family_bucket in results.items():
        for setup, bucket in family_bucket.items():
            filled = int(bucket["filled"])
            if filled < 25:
                continue
            wins = int(bucket["wins"])
            clean = int(bucket["clean_runs"])
            reclaimed = int(bucket["reclaimed_runs"])
            stopped = int(bucket["stopped"])
            timed_out = int(bucket["timed_out"])
            flip = int(bucket["flip_runs_after_stop"])
            avg_risk = float(bucket["sum_risk_pct"]) / filled if filled else 0.0
            avg_score = float(bucket["sum_score"]) / max(1, int(bucket["trades"]))
            win_rate = wins / filled
            reclaimed_share = reclaimed / max(1, wins)
            flip_rate = flip / max(1, stopped)
            leaderboard.append(
                {
                    "family_direction": family_direction,
                    "setup": setup,
                    "filled": filled,
                    "wins": wins,
                    "win_rate": round(win_rate * 100.0, 2),
                    "win_rate_ci95": wilson_ci95(wins, filled),
                    "avg_net_r": round(float(bucket["sum_net_r"]) / filled, 4),
                    "clean_runs": clean,
                    "reclaimed_runs": reclaimed,
                    "reclaimed_share": round(reclaimed_share * 100.0, 2),
                    "stopped": stopped,
                    "timed_out": timed_out,
                    "flip_runs_after_stop": flip,
                    "flip_rate_after_stop": round(flip_rate * 100.0, 2),
                    "avg_risk_pct": round(avg_risk, 4),
                    "avg_signal_score": round(avg_score, 2),
                    "sample": bucket["sample"],
                }
            )

    leaderboard.sort(
        key=lambda item: (
            item["win_rate"],
            item["filled"],
            item["reclaimed_share"],
            -item["avg_risk_pct"],
        ),
        reverse=True,
    )

    all_transitions = mine_signal_transitions(list(best_signals.values()))
    today_transition_list = mine_signal_transitions(list(today_signals.values()))
    transition_counts = collections.Counter(item["type"] for item in all_transitions)
    transition_leaderboard_full = evaluate_transition_families(
        rows_1m, all_transitions, fee_bps=fee_bps, slippage_bps=slippage_bps
    )
    transition_family_summary = summarize_transition_families(transition_leaderboard_full)
    transition_context_edges = summarize_transition_context_edges(transition_family_summary)
    reclaim_threshold_sweeps = summarize_reclaim_threshold_sweeps(
        transition_family_summary, train_until_ts=train_cutoff_ts
    )
    robust_transition_gates = summarize_robust_transition_gates(
        transition_family_summary, train_until_ts=train_cutoff_ts
    )
    transition_gate_profiles = build_transition_gate_profiles(robust_transition_gates)
    walk_forward_transitions = summarize_walk_forward_validation(transition_leaderboard_full)
    gated_transitions = [
        item
        for item in all_transitions
        if transition_gate_passes(str(item["type"]), item["secondSignal"], transition_gate_profiles)
    ]
    gated_transition_counts = collections.Counter(item["type"] for item in gated_transitions)
    gated_transition_keep_rates = summarize_transition_gate_keep_rates(all_transitions, gated_transitions)
    gated_transition_leaderboard_full = evaluate_transition_families(
        rows_1m, gated_transitions, fee_bps=fee_bps, slippage_bps=slippage_bps
    )
    gated_transition_family_summary = summarize_transition_families(gated_transition_leaderboard_full)
    transition_leaderboard_public = [
        {key: value for key, value in item.items() if key != "records"} for item in transition_leaderboard_full[:24]
    ]
    transition_family_summary_public = [
        {key: value for key, value in item.items() if key != "records"} for item in transition_family_summary
    ]
    gated_transition_leaderboard_public = [
        {key: value for key, value in item.items() if key != "records"} for item in gated_transition_leaderboard_full[:24]
    ]
    gated_transition_family_summary_public = [
        {key: value for key, value in item.items() if key != "records"} for item in gated_transition_family_summary
    ]
    family_context_summary = summarize_family_contexts(candidates)
    if hasattr(conn, "close"):
        conn.close()

    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dbPath": db_path,
        "backend": "duckdb" if (use_warehouse or db_path.endswith(".duckdb")) else "sqlite",
        "sinceTs": since_ts,
        "untilTs": until_ts,
        "totalCandles1m": len(rows_1m),
        "candidateCount": len(candidates),
        "signalThreshold": signal_threshold,
        "cooldownBars": cooldown_bars,
        "feeBps": fee_bps,
        "slippageBps": slippage_bps,
        "trainFraction": train_fraction,
        "trainCutoffTs": train_cutoff_ts,
        "familySignalCounts": dict(sorted(family_signal_counts.items())),
        "transitionCounts": dict(sorted(transition_counts.items())),
        "leaderboard": leaderboard[:18],
        "transitionLeaderboard": transition_leaderboard_public,
        "transitionFamilies": transition_family_summary_public,
        "transitionContextEdges": transition_context_edges,
        "reclaimThresholdSweeps": reclaim_threshold_sweeps,
        "robustTransitionGates": robust_transition_gates,
        "transitionGateProfiles": transition_gate_profiles,
        "walkForwardTransitions": walk_forward_transitions,
        "gatedTransitionCounts": dict(sorted(gated_transition_counts.items())),
        "gatedTransitionKeepRates": gated_transition_keep_rates,
        "gatedTransitionLeaderboard": gated_transition_leaderboard_public,
        "gatedTransitionFamilies": gated_transition_family_summary_public,
        "familyContexts": family_context_summary,
        "sampleCases": sample_cases[:16],
        "todayCases": today_cases[:24],
        "todaySignals": sorted(today_signals.values(), key=lambda item: (item["score"], item["timestamp"]), reverse=True)[:40],
        "signalTransitions": all_transitions[:40],
        "todayTransitions": today_transition_list[:20],
    }


def markdown_report(report: dict[str, object]) -> str:
    lines = [
        "# Fakeout / Reclaim / Breakout Research",
        "",
        f"- Generated: `{report['generatedAt']}`",
        f"- 1m candles scanned: `{report['totalCandles1m']}`",
        f"- candidate signals: `{report['candidateCount']}`",
        f"- signal threshold: `{report['signalThreshold']}`",
        f"- cooldown bars: `{report['cooldownBars']}`",
        f"- costs: `{report['feeBps']}` bps fee + `{report['slippageBps']}` bps slippage per side",
        f"- train fraction: `{report['trainFraction']}` (cutoff ts `{report['trainCutoffTs']}`)",
        "",
    ]
    lines.extend(["## Signal Families", ""])
    for family_direction, count in report["familySignalCounts"].items():
        lines.append(f"- `{family_direction}`: `{count}` signals")
    lines.extend(["", "## Top Setups", ""])
    if report.get("familyContexts"):
        lines.extend(["## Family Context", ""])
        for item in report["familyContexts"][:12]:
            lines.extend(
                [
                    f"### {item['familyDirection']}",
                    f"- count: `{item['count']}`",
                    f"- avg score: `{item['avgScore']}`",
                    f"- avg direction margin: `{item['avgDirectionMargin']}`",
                    f"- avg regime margin: `{item['avgRegimeMargin']}`",
                    f"- avg micro margin: `{item['avgMicroMargin']}`",
                    f"- avg lifecycle margin: `{item['avgLifecycleMargin']}`",
                    f"- avg lifecycle pressure: `{item['avgLifecyclePressure']}`",
                    f"- avg lifecycle maturity: `{item['avgLifecycleMaturity']}`",
                    f"- avg volume margin: `{item['avgVolumeMargin']}`",
                    f"- avg spot margin: `{item['avgSpotMargin']}`",
                    f"- avg futures margin: `{item['avgFuturesMargin']}`",
                    f"- avg lead margin: `{item['avgLeadMargin']}`",
                    f"- avg whale margin: `{item['avgWhaleMargin']}`",
                    f"- avg breakout force: `{item['avgBreakoutForce']}`",
                    f"- avg rejection force: `{item['avgRejectionForce']}`",
                    f"- avg compression force: `{item['avgCompressionForce']}`",
                    "",
                ]
            )
        lines.extend(["## Top Setups", ""])
    for item in report["leaderboard"]:
        lines.extend(
            [
                f"### {item['family_direction']} · {item['setup']}",
                f"- filled: `{item['filled']}`",
                f"- win rate: `{item['win_rate']}%` (95% CI `{item['win_rate_ci95'][0]}`-`{item['win_rate_ci95'][1]}`)",
                f"- avg net R: `{item['avg_net_r']}`",
                f"- clean runs: `{item['clean_runs']}`",
                f"- reclaimed runs: `{item['reclaimed_runs']}` (`{item['reclaimed_share']}%` der Gewinner)",
                f"- stopped: `{item['stopped']}`",
                f"- flip runs after stop: `{item['flip_runs_after_stop']}` (`{item['flip_rate_after_stop']}%` der Stops)",
                f"- avg risk: `{item['avg_risk_pct']}%`",
                f"- avg signal score: `{item['avg_signal_score']}`",
                "",
            ]
        )
    if report.get("transitionLeaderboard"):
        lines.extend(["## Transition Setups", ""])
        for item in report["transitionLeaderboard"]:
            lines.extend(
                [
                    f"### {item['transitionType']} · {item['setup']}",
                    f"- filled: `{item['filled']}` / `{item['count']}`",
                    f"- win rate: `{item['winRate']}%` (95% CI `{item['winRateCi95'][0]}`-`{item['winRateCi95'][1]}`)",
                    f"- avg net R: `{item['avgNetR']}`",
                    f"- clean runs: `{item['cleanRuns']}`",
                    f"- reclaimed runs: `{item['reclaimedRuns']}`",
                    f"- stopped: `{item['stopped']}`",
                    f"- flip runs after stop: `{item['flipRunsAfterStop']}` (`{item['flipRateAfterStop']}%` der Stops)",
                    f"- avg risk: `{item['avgRiskPct']}%`",
                    f"- avg first score: `{item['avgFirstScore']}`",
                    f"- avg second score: `{item['avgSecondScore']}`",
                    "",
                ]
            )
    if report.get("transitionFamilies"):
        lines.extend(["## Transition Families", ""])
        for item in report["transitionFamilies"]:
            lines.extend(
                [
                    f"### {item['transitionType']}",
                    f"- best setup: `{item['bestSetup']}`",
                    f"- filled: `{item['filled']}`",
                    f"- win rate: `{item['winRate']}%` (95% CI `{item['winRateCi95'][0]}`-`{item['winRateCi95'][1]}`)",
                    f"- avg net R: `{item['avgNetR']}`",
                    f"- clean runs: `{item['cleanRuns']}`",
                    f"- reclaimed runs: `{item['reclaimedRuns']}`",
                    f"- stopped: `{item['stopped']}`",
                    f"- flip runs after stop: `{item['flipRunsAfterStop']}` (`{item['flipRateAfterStop']}%` der Stops)",
                    f"- avg risk: `{item['avgRiskPct']}%`",
                    f"- avg first score: `{item['avgFirstScore']}`",
                    f"- avg second score: `{item['avgSecondScore']}`",
                    "",
                ]
            )
    if report.get("transitionContextEdges"):
        lines.extend(["## Transition Context Edges", ""])
        for item in report["transitionContextEdges"][:12]:
            lines.extend(
                [
                    f"### {item['transitionType']} · {item['bestSetup']}",
                    f"- filled: `{item['filled']}`",
                    f"- win rate: `{item['winRate']}%`",
                    f"- top positive edges: "
                    + ", ".join(f"`{key} {value:+.2f}`" for key, value in item["topPositiveEdges"]),
                    f"- top negative edges: "
                    + ", ".join(f"`{key} {value:+.2f}`" for key, value in item["topNegativeEdges"]),
                    "",
                ]
            )
    if report.get("reclaimThresholdSweeps"):
        lines.extend(["## Reclaim Threshold Sweeps", ""])
        for item in report["reclaimThresholdSweeps"]:
            lines.extend(
                [
                    f"### {item['transitionType']} · {item['bestSetup']}",
                    f"- baseline: `{item['baselineWins']}/{item['baselineFilled']}` · `{item['baselineWinRate']}%`",
                ]
            )
            for sweep in item["sweeps"]:
                comparator = ">=" if sweep["mode"] == "min" else "<="
                lines.append(
                    f"- `{sweep['metric']} {comparator} {sweep['threshold']}` -> `{sweep['wins']}/{sweep['filled']}` · `{sweep['winRate']}%` (`{sweep['deltaVsBaseline']:+.2f}pp`)"
                )
            lines.append("")
    if report.get("robustTransitionGates"):
        lines.extend(["## Robust Transition Gates", ""])
        for item in report["robustTransitionGates"]:
            lines.append(f"### {item['transitionType']}")
            for gate in item["bestGates"][:5]:
                comparator = ">=" if gate["mode"] == "min" else "<="
                lines.append(
                    f"- `{gate['metric']} {comparator} {gate['threshold']}` -> uplift `{gate['meanUplift']:+.2f}pp`, gated `{gate['gatedWinRate']}%`, keep `{gate['keepRate']}%`, positive slices `{gate['positiveShare']}%`"
                )
            lines.append("")
    if report.get("transitionGateProfiles"):
        lines.extend(["## Transition Gate Profiles", ""])
        for transition_type, profile in report["transitionGateProfiles"].items():
            comparator = ">=" if profile["mode"] == "min" else "<="
            lines.append(
                f"- `{transition_type}`: `{profile['metric']} {comparator} {profile['threshold']}` "
                f"(uplift `{profile['meanUplift']:+.2f}pp`, keep `{profile['keepRate']}%`, positive slices `{profile['positiveShare']}%`)"
            )
        lines.append("")
    if report.get("walkForwardTransitions"):
        lines.extend(["## Walk-Forward Validation", ""])
        for item in report["walkForwardTransitions"]:
            lines.extend(
                [
                    f"### {item['transitionType']}",
                    f"- buckets evaluated: `{item['bucketsEvaluated']}`",
                    f"- baseline: `{item['baselineWins']}/{item['baselineFilled']}` · `{item['baselineWinRate']}%` (95% CI `{item['baselineWinRateCi95'][0]}`-`{item['baselineWinRateCi95'][1]}`) · avg net R `{item['avgBaselineNetR']}`",
                    f"- gated: `{item['gatedWins']}/{item['gatedFilled']}` · `{item['gatedWinRate']}%` (95% CI `{item['gatedWinRateCi95'][0]}`-`{item['gatedWinRateCi95'][1]}`) · avg net R `{item['avgGatedNetR']}`",
                    f"- avg uplift: `{item['avgUplift']:+.2f}pp`",
                    f"- positive buckets: `{item['positiveBuckets']}` / `{item['bucketsEvaluated']}` (`{item['positiveBucketShare']}%`)",
                    f"- avg keep rate: `{item['avgKeepRate']}%`",
                    f"- most common train setup: `{item['mostCommonSetup']}`",
                    "",
                ]
            )
    if report["todaySignals"]:
        lines.extend(["## Heutige Signale", ""])
        for case in report["todaySignals"]:
            lines.append(
                f"- `{case['time']}` · `{case['family']}` · `{case['direction']}` · `{case['bestSetup']}` · `{case['bestResult']}` · score `{case['score']}`"
            )
        lines.append("")
    if report.get("transitionCounts"):
        lines.extend(["## Transition Counts", ""])
        for transition_type, count in report["transitionCounts"].items():
            lines.append(f"- `{transition_type}`: `{count}`")
        lines.append("")
    if report.get("gatedTransitionKeepRates"):
        lines.extend(["## Gated Transition Keep Rates", ""])
        for item in report["gatedTransitionKeepRates"][:16]:
            lines.append(
                f"- `{item['transitionType']}`: kept `{item['kept']}/{item['total']}` (`{item['keepRate']}%`)"
            )
        lines.append("")
    if report.get("gatedTransitionFamilies"):
        lines.extend(["## Gated Transition Families", ""])
        for item in report["gatedTransitionFamilies"][:12]:
            lines.extend(
                [
                    f"### {item['transitionType']}",
                    f"- best setup: `{item['bestSetup']}`",
                    f"- filled: `{item['filled']}`",
                    f"- win rate: `{item['winRate']}%` (95% CI `{item['winRateCi95'][0]}`-`{item['winRateCi95'][1]}`)",
                    f"- avg net R: `{item['avgNetR']}`",
                    f"- clean runs: `{item['cleanRuns']}`",
                    f"- reclaimed runs: `{item['reclaimedRuns']}`",
                    f"- stopped: `{item['stopped']}`",
                    f"- avg risk: `{item['avgRiskPct']}%`",
                    "",
                ]
            )
    if report.get("todayTransitions"):
        lines.extend(["## Heutige Transitionen", ""])
        for item in report["todayTransitions"]:
            lines.append(
                f"- `{item['start']} -> {item['end']}` · `{item['type']}` · gap `{item['gapMin']}m` "
                f"· first `{item['first']['bestResult']}` score `{item['first']['score']}` "
                f"· second `{item['second']['bestResult']}` score `{item['second']['score']}`"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--warehouse", action="store_true")
    parser.add_argument("--signal-threshold", type=float, default=14.0)
    parser.add_argument("--cooldown-bars", type=int, default=10)
    parser.add_argument("--since-ts", type=int)
    parser.add_argument("--until-ts", type=int)
    parser.add_argument(
        "--fee-bps",
        type=float,
        default=5.0,
        help="taker fee per side in basis points, charged on entry and exit (default: 5.0)",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=5.0,
        help="slippage per side in basis points, charged on entry and exit (default: 5.0)",
    )
    parser.add_argument(
        "--train-fraction",
        type=float,
        default=0.65,
        help="fraction of 1m bars used as train prefix for percentile thresholds "
        "and gate/sweep estimation; later bars are scored with frozen thresholds (default: 0.65)",
    )
    parser.add_argument("--json-out")
    parser.add_argument("--md-out")
    args = parser.parse_args()

    report = run_research(
        db_path=args.db,
        signal_threshold=args.signal_threshold,
        cooldown_bars=args.cooldown_bars,
        since_ts=args.since_ts,
        until_ts=args.until_ts,
        use_warehouse=args.warehouse,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        train_fraction=args.train_fraction,
    )

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=True, indent=2))
    if args.md_out:
        Path(args.md_out).write_text(markdown_report(report))

    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
