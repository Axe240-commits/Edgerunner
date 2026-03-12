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
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from db import DEFAULT_DB_PATH, _connect, _table_name


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

FEATURE_COLUMNS = (
    "timestamp, open, high, low, close, total_range, body_ratio, wick_ratio, "
    "body_position, delta_pct, vol_vs_ma, cluster_range_atr, cluster_spread, "
    "bos_bull, bos_bear, choch, bull_div, bear_div, bull_div_streak, bear_div_streak, "
    "is_seeker_hs, is_seeker_ls, is_seeker_div_hs, is_seeker_div_ls, seeker_div_nr, "
    "is_seeker_kill, killed_seekers_count, killed_seeker_divs, killed_seekers_age_min, "
    "killed_seekers_age_max, break_depth, swing_age, sw_bullish, same_dir, "
    "bos_body, bos_wick, delta_vs_ma, "
    "spot_volume, futures_volume, futures_minus_spot_volume, "
    "dist_swing_high, dist_swing_low, seeker_zone_size, seeker_zone_vs_body, "
    "seeker_wick_dominance, htf_trend, htf_bos"
)


@dataclass
class TfThresholds:
    tight_cluster: float
    quiet_volume: float
    quiet_spread: float


@dataclass
class TfCursor:
    rows: list[sqlite3.Row]
    index: int = 0

    def advance_to(self, ts: int) -> sqlite3.Row | None:
        while self.index + 1 < len(self.rows) and int(self.rows[self.index + 1]["timestamp"]) <= ts:
            self.index += 1
        if self.rows and int(self.rows[self.index]["timestamp"]) <= ts:
            return self.rows[self.index]
        return None


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * p))
    return float(ordered[max(0, min(len(ordered) - 1, idx))])


def to_num(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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
    conn: sqlite3.Connection,
    tf: str,
    since_ts: int | None = None,
    until_ts: int | None = None,
) -> list[sqlite3.Row]:
    table = _table_name(tf)
    if since_ts is None and until_ts is None:
        return conn.execute(
            f"SELECT {FEATURE_COLUMNS} FROM {table} ORDER BY timestamp"
        ).fetchall()
    clauses = []
    params: list[int] = []
    if since_ts is not None:
        clauses.append("timestamp >= ?")
        params.append(since_ts)
    if until_ts is not None:
        clauses.append("timestamp <= ?")
        params.append(until_ts)
    where = " AND ".join(clauses)
    return conn.execute(
        f"SELECT {FEATURE_COLUMNS} FROM {table} WHERE {where} ORDER BY timestamp",
        tuple(params),
    ).fetchall()


def build_thresholds(rows: Iterable[sqlite3.Row]) -> TfThresholds:
    cluster = [to_num(row["cluster_range_atr"]) for row in rows if row["cluster_range_atr"] is not None]
    volume = [to_num(row["vol_vs_ma"]) for row in rows if row["vol_vs_ma"] is not None]
    spread = [to_num(row["cluster_spread"]) for row in rows if row["cluster_spread"] is not None]
    return TfThresholds(
        tight_cluster=percentile(cluster, 0.35),
        quiet_volume=percentile(volume, 0.50),
        quiet_spread=percentile(spread, 0.40),
    )


def row_tight_range(row: sqlite3.Row, thresholds: TfThresholds) -> bool:
    return (
        to_num(row["cluster_range_atr"]) <= thresholds.tight_cluster
        and to_num(row["vol_vs_ma"], 1.0) <= thresholds.quiet_volume
        and to_num(row["cluster_spread"]) <= thresholds.quiet_spread
    )


def score_snapshot(tf: str, row: sqlite3.Row, thresholds: TfThresholds) -> dict[str, float]:
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


def cycle_metrics(conn: sqlite3.Connection, tf: str, ts: int, price: float) -> dict[str, float]:
    row = conn.execute(
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
                    WHEN last_kill_ts IS NOT NULL AND last_kill_ts <= :ts THEN 'killed'
                    ELSE 'open'
                END AS eff_status
            FROM seeker_cycles
            WHERE timeframe = :tf
              AND origin_ts <= :ts
        )
        SELECT
            COALESCE(SUM(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom <= :price AND zone_top >= :price THEN 1 ELSE 0 END), 0) AS inside_open_hs,
            COALESCE(SUM(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_bottom <= :price AND zone_top >= :price THEN 1 ELSE 0 END), 0) AS inside_open_ls,
            COALESCE(SUM(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom <= :price AND zone_top >= :price THEN 1 ELSE 0 END), 0) AS inside_killed_hs,
            COALESCE(SUM(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_bottom <= :price AND zone_top >= :price THEN 1 ELSE 0 END), 0) AS inside_killed_ls,
            MIN(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom >= :price THEN zone_bottom - :price END) AS dist_open_hs_above,
            MIN(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_top <= :price THEN :price - zone_top END) AS dist_open_ls_below,
            MIN(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom >= :price THEN zone_bottom - :price END) AS dist_killed_hs_above,
            MIN(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_top <= :price THEN :price - zone_top END) AS dist_killed_ls_below,
            COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'HS' AND zone_bottom <= :price AND zone_top >= :price THEN div_count_total ELSE 0 END), 0) AS max_open_hs_divs,
            COALESCE(MAX(CASE WHEN eff_status = 'open' AND cycle_type = 'LS' AND zone_bottom <= :price AND zone_top >= :price THEN div_count_total ELSE 0 END), 0) AS max_open_ls_divs,
            COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'HS' AND zone_bottom <= :price AND zone_top >= :price THEN age_bars ELSE 0 END), 0) AS max_killed_hs_age_bars,
            COALESCE(MAX(CASE WHEN eff_status = 'killed' AND cycle_type = 'LS' AND zone_bottom <= :price AND zone_top >= :price THEN age_bars ELSE 0 END), 0) AS max_killed_ls_age_bars
        FROM active
        """,
        {"tf": tf, "ts": ts, "price": price},
    ).fetchone()
    return {key: to_num(row[key]) for key in row.keys()}


def build_cycle_metric_cache(
    conn: sqlite3.Connection,
    rows_by_tf: dict[str, list[sqlite3.Row]],
) -> dict[str, dict[int, dict[str, float]]]:
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


def volume_breaker_score(tf: str, row: sqlite3.Row) -> dict[str, float]:
    weight = TF_WEIGHT[tf]
    bull = 0.0
    bear = 0.0
    breakout = 0.0
    rejection = 0.0

    vol_vs_ma = to_num(row["vol_vs_ma"], 1.0)
    futures_lead = to_num(row["futures_minus_spot_volume"])
    delta_pct = to_num(row["delta_pct"])
    bos_body = int(to_num(row["bos_body"]))
    bos_wick = int(to_num(row["bos_wick"]))
    dist_high = to_num(row["dist_swing_high"])
    dist_low = to_num(row["dist_swing_low"])

    if vol_vs_ma >= 1.25:
        breakout += 0.45 * weight
        bull += 0.12 * weight if delta_pct > 0 else 0.0
        bear += 0.12 * weight if delta_pct < 0 else 0.0
    if vol_vs_ma >= 1.6:
        breakout += 0.3 * weight

    if futures_lead > 0:
        bull += 0.2 * weight
    elif futures_lead < 0:
        bear += 0.2 * weight

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

    return {
        "bull": bull,
        "bear": bear,
        "breakout": breakout,
        "rejection": rejection,
    }


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
    direction_margin = bull - bear
    micro_margin = cycle_total["micro_bull"] - cycle_total["micro_bear"]
    conflict = cycle_total["conflict"]
    breakout_force = volume_total["breakout"]

    if compression >= 6.0 and conflict <= 3.8:
        if bear >= 12.0 and direction_margin <= -2.0 and micro_margin <= -1.5:
            return "zone_fade", "short"
        if bull >= 12.0 and direction_margin >= 2.0 and micro_margin >= 1.5:
            return "zone_fade", "long"

    if int(to_num(row["bos_bull"])) == 1 or int(to_num(row["sw_bullish"])) == 1 or (int(to_num(row["choch"])) == 1 and to_num(row["delta_pct"]) > 0):
        if (
            bull >= 10.5
            and direction_margin >= 1.5
            and recent_bear_max >= 8.0
            and (micro_margin >= 0.35 or breakout_force >= 2.0)
        ):
            return "reclaim_run", "long"
        if bull >= 10.5 and direction_margin >= 1.2 and breakout_force >= 2.2 and micro_margin >= 0.5:
            return "breaker_run", "long"

    if int(to_num(row["bos_bear"])) == 1 or (int(to_num(row["choch"])) == 1 and to_num(row["delta_pct"]) < 0):
        if (
            bear >= 10.5
            and direction_margin <= -1.5
            and recent_bull_max >= 8.0
            and (micro_margin <= -0.35 or breakout_force >= 2.0)
        ):
            return "reclaim_run", "short"
        if bear >= 10.5 and direction_margin <= -1.2 and breakout_force >= 2.2 and micro_margin <= -0.5:
            return "breaker_run", "short"

    if compression >= 5.0 and conflict >= 3.2:
        if cycle_total["micro_bear"] >= 4.0 and bull >= bear:
            return "micro_fakeout", "short"
        if cycle_total["micro_bull"] >= 4.0 and bear >= bull:
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


def find_recent_structure_stop(
    conn: sqlite3.Connection,
    ts: int,
    direction: str,
    lookback: int = 8,
) -> float | None:
    rows = conn.execute(
        "SELECT high, low FROM candles_1m WHERE timestamp < ? ORDER BY timestamp DESC LIMIT ?",
        (ts, lookback),
    ).fetchall()
    if not rows:
        return None
    if direction == "long":
        return min(to_num(row["low"]) for row in rows)
    return max(to_num(row["high"]) for row in rows)


def evaluate_trade(
    conn: sqlite3.Connection,
    signal: dict,
    entry_model: str,
    stop_model: str,
    target_model: str,
    horizon_bars: int = 90,
    fill_bars: int = 20,
) -> dict[str, object]:
    ts = int(signal["timestamp"])
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
        structure = find_recent_structure_stop(conn, ts, direction)
        if structure is None:
            return {"filled": False, "result": "no_structure_stop"}
        stop_price = structure
        risk = entry_price - stop_price if direction == "long" else stop_price - entry_price
        if risk <= 0:
            return {"filled": False, "result": "invalid_structure_stop"}

    target_r = TARGET_MODELS[target_model]
    target_price = entry_price + risk * target_r if direction == "long" else entry_price - risk * target_r

    future = conn.execute(
        "SELECT timestamp, open, high, low, close FROM candles_1m WHERE timestamp > ? ORDER BY timestamp LIMIT ?",
        (ts, horizon_bars),
    ).fetchall()
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
                structure = find_recent_structure_stop(conn, ts, direction)
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
        "flip_run_after_stop": flip_run_after_stop,
    }


def aggregate_candidate_context(
    row: sqlite3.Row,
    mtf_rows: dict[str, sqlite3.Row],
    thresholds: dict[str, TfThresholds],
) -> dict[str, object]:
    total = {"bull": 0.0, "bear": 0.0, "compression": 0.0, "event": 0.0}
    per_tf: dict[str, dict[str, float]] = {}
    for tf, tf_row in {"1m": row, **mtf_rows}.items():
        if tf_row is None:
            continue
        snapshot = score_snapshot(tf, tf_row, thresholds[tf])
        per_tf[tf] = snapshot
        for key in total:
            total[key] += snapshot[key]
    total["direction_margin"] = total["bull"] - total["bear"]
    total["per_tf"] = per_tf
    return total


def aggregate_cycle_context(
    cycle_cache: dict[str, dict[int, dict[str, float]]],
    row: sqlite3.Row,
    mtf_rows: dict[str, sqlite3.Row],
) -> dict[str, object]:
    total = {"regime_bull": 0.0, "regime_bear": 0.0, "micro_bull": 0.0, "micro_bear": 0.0, "conflict": 0.0}
    per_tf: dict[str, dict[str, float]] = {}
    for tf, tf_row in {"1m": row, **mtf_rows}.items():
        if tf_row is None:
            continue
        metrics = cycle_cache[tf][int(tf_row["timestamp"])]
        score = cycle_score(tf, metrics)
        per_tf[tf] = {**metrics, **score}
        for key in total:
            total[key] += score[key]
    total["per_tf"] = per_tf
    return total


def aggregate_volume_breaker_context(
    row: sqlite3.Row,
    mtf_rows: dict[str, sqlite3.Row],
) -> dict[str, object]:
    total = {"bull": 0.0, "bear": 0.0, "breakout": 0.0, "rejection": 0.0}
    per_tf: dict[str, dict[str, float]] = {}
    for tf, tf_row in {"1m": row, **mtf_rows}.items():
        if tf_row is None:
            continue
        score = volume_breaker_score(tf, tf_row)
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
    pieces.append(f"vol {volume_total['bull'] - volume_total['bear']:.1f}")
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


def evaluate_transition_families(
    conn: sqlite3.Connection,
    transitions: list[dict[str, object]],
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
                            "sumFirstScore": 0.0,
                            "sumSecondScore": 0.0,
                            "samples": [],
                        },
                    )
                    bucket["count"] += 1
                    bucket["sumFirstScore"] += float(transition["first"]["score"])
                    bucket["sumSecondScore"] += float(transition["second"]["score"])
                    outcome = evaluate_trade(
                        conn=conn,
                        signal=second_signal,
                        entry_model=entry_model,
                        stop_model=stop_model,
                        target_model=target_model,
                    )
                    if not outcome.get("filled"):
                        continue
                    bucket["filled"] += 1
                    bucket["sumRiskPct"] += float(outcome.get("risk_pct") or 0.0)
                    result = str(outcome["result"])
                    if result in ("clean_run", "reclaimed_run"):
                        bucket["wins"] += 1
                    if result == "clean_run":
                        bucket["cleanRuns"] += 1
                    elif result == "reclaimed_run":
                        bucket["reclaimedRuns"] += 1
                    elif result == "stopped":
                        bucket["stopped"] += 1
                    else:
                        bucket["timedOut"] += 1
                    if outcome.get("flip_run_after_stop"):
                        bucket["flipRunsAfterStop"] += 1
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
        leaderboard.append(
            {
                "transitionType": bucket["transitionType"],
                "setup": bucket["setup"],
                "count": int(bucket["count"]),
                "filled": filled,
                "wins": wins,
                "winRate": round(wins / filled * 100.0, 2),
                "cleanRuns": int(bucket["cleanRuns"]),
                "reclaimedRuns": int(bucket["reclaimedRuns"]),
                "stopped": stopped,
                "timedOut": int(bucket["timedOut"]),
                "flipRunsAfterStop": int(bucket["flipRunsAfterStop"]),
                "flipRateAfterStop": round(int(bucket["flipRunsAfterStop"]) / max(1, stopped) * 100.0, 2),
                "avgRiskPct": round(float(bucket["sumRiskPct"]) / filled, 4),
                "avgFirstScore": round(float(bucket["sumFirstScore"]) / max(1, int(bucket["count"])), 2),
                "avgSecondScore": round(float(bucket["sumSecondScore"]) / max(1, int(bucket["count"])), 2),
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
                "cleanRuns": item["cleanRuns"],
                "reclaimedRuns": item["reclaimedRuns"],
                "stopped": item["stopped"],
                "timedOut": item["timedOut"],
                "flipRunsAfterStop": item["flipRunsAfterStop"],
                "flipRateAfterStop": item["flipRateAfterStop"],
                "avgRiskPct": item["avgRiskPct"],
                "avgFirstScore": item["avgFirstScore"],
                "avgSecondScore": item["avgSecondScore"],
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


def run_research(
    db_path: str,
    signal_threshold: float,
    cooldown_bars: int,
    since_ts: int | None = None,
    until_ts: int | None = None,
) -> dict[str, object]:
    conn = _connect(db_path)
    higher_rows = {
        tf: load_timeframe_rows(conn, tf, since_ts=since_ts, until_ts=until_ts)
        for tf in HIGHER_TFS
    }
    thresholds = {}
    if since_ts is None and until_ts is None:
        rows_1m = conn.execute(f"SELECT {FEATURE_COLUMNS} FROM candles_1m ORDER BY timestamp").fetchall()
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
        rows_1m = conn.execute(
            f"SELECT {FEATURE_COLUMNS} FROM candles_1m WHERE {where} ORDER BY timestamp",
            tuple(params),
        ).fetchall()
    thresholds["1m"] = build_thresholds(rows_1m)
    for tf, rows in higher_rows.items():
        thresholds[tf] = build_thresholds(rows)
    cycle_cache = build_cycle_metric_cache(conn, {"1m": rows_1m, **higher_rows})

    cursors = {tf: TfCursor(rows=rows) for tf, rows in higher_rows.items()}

    recent_bull: collections.deque[float] = collections.deque(maxlen=30)
    recent_bear: collections.deque[float] = collections.deque(maxlen=30)
    last_signal_bar: dict[tuple[str, str], int] = {}
    candidates: list[dict[str, object]] = []
    family_signal_counts: collections.Counter[str] = collections.Counter()

    for bar_index, row in enumerate(rows_1m):
        ts = int(row["timestamp"])
        mtf_rows = {tf: cursor.advance_to(ts) for tf, cursor in cursors.items()}
        aggregate = aggregate_candidate_context(row, mtf_rows, thresholds)
        cycle_total = aggregate_cycle_context(cycle_cache, row, mtf_rows)
        volume_total = aggregate_volume_breaker_context(row, mtf_rows)
        signal_family, direction = determine_signal_family(
            row=row,
            aggregate=aggregate,
            recent_bull_max=max(recent_bull) if recent_bull else 0.0,
            recent_bear_max=max(recent_bear) if recent_bear else 0.0,
            cycle_total=cycle_total,
            volume_total=volume_total,
        )
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
                            "sum_score": 0.0,
                            "sample": [],
                        },
                    )
                    bucket["trades"] += 1
                    bucket["sum_score"] += float(signal["score"])
                    outcome = evaluate_trade(
                        conn=conn,
                        signal=signal,
                        entry_model=entry_model,
                        stop_model=stop_model,
                        target_model=target_model,
                    )
                    if not outcome.get("filled"):
                        continue
                    bucket["filled"] += 1
                    bucket["sum_risk_pct"] += float(outcome.get("risk_pct") or 0.0)
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
                                "breakout_force": round(float(signal["volume_total"]["breakout"]), 4),
                                "rejection_force": round(float(signal["volume_total"]["rejection"]), 4),
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
                        "breakout_force": round(float(signal["volume_total"]["breakout"]), 4),
                        "rejection_force": round(float(signal["volume_total"]["rejection"]), 4),
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
    transition_leaderboard_full = evaluate_transition_families(conn, all_transitions)
    transition_family_summary = summarize_transition_families(transition_leaderboard_full)
    conn.close()

    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dbPath": db_path,
        "sinceTs": since_ts,
        "untilTs": until_ts,
        "totalCandles1m": len(rows_1m),
        "candidateCount": len(candidates),
        "signalThreshold": signal_threshold,
        "cooldownBars": cooldown_bars,
        "familySignalCounts": dict(sorted(family_signal_counts.items())),
        "transitionCounts": dict(sorted(transition_counts.items())),
        "leaderboard": leaderboard[:18],
        "transitionLeaderboard": transition_leaderboard_full[:24],
        "transitionFamilies": transition_family_summary,
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
        "",
    ]
    lines.extend(["## Signal Families", ""])
    for family_direction, count in report["familySignalCounts"].items():
        lines.append(f"- `{family_direction}`: `{count}` signals")
    lines.extend(["", "## Top Setups", ""])
    for item in report["leaderboard"]:
        lines.extend(
            [
                f"### {item['family_direction']} · {item['setup']}",
                f"- filled: `{item['filled']}`",
                f"- win rate: `{item['win_rate']}%`",
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
                    f"- win rate: `{item['winRate']}%`",
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
                    f"- win rate: `{item['winRate']}%`",
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
    parser.add_argument("--signal-threshold", type=float, default=14.0)
    parser.add_argument("--cooldown-bars", type=int, default=10)
    parser.add_argument("--since-ts", type=int)
    parser.add_argument("--until-ts", type=int)
    parser.add_argument("--json-out")
    parser.add_argument("--md-out")
    args = parser.parse_args()

    report = run_research(
        db_path=args.db,
        signal_threshold=args.signal_threshold,
        cooldown_bars=args.cooldown_bars,
        since_ts=args.since_ts,
        until_ts=args.until_ts,
    )

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=True, indent=2))
    if args.md_out:
        Path(args.md_out).write_text(markdown_report(report))

    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
