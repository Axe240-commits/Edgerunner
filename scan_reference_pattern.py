#!/usr/bin/env python3
"""
Scan history for tolerant reference-pattern matches.

This is a research scanner, not a production trigger.
It mirrors a reference profile into long/short variants and scores:
- initial breakout quality
- post-breakout liquidity push
- hold / acceptance
- compression quality
- late pressure / divergence
- release trigger quality
- M1 confirmation bursts
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo


DB_PATH = Path("/home/axe240/Projects/edgerunner_restored/edgerunner.db")
OUT_DIR = Path("/home/axe240/Projects/edgerunner_restored/.runtime")
BERLIN = ZoneInfo("Europe/Berlin")
TF_ORDER = ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]
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


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def band_score(value: float, low: float, peak: float, high: float) -> float:
    if value <= low or value >= high:
        return 0.0
    if abs(value - peak) < 1e-9:
        return 1.0
    if value < peak:
        return (value - low) / max(1e-9, peak - low)
    return (high - value) / max(1e-9, high - peak)


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, BERLIN).strftime("%d.%m.%Y %H:%M")


def sign_direction(direction: str) -> int:
    return 1 if direction == "long" else -1


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def lower_tf(tf: str) -> str:
    idx = TF_ORDER.index(tf)
    return TF_ORDER[max(0, idx - 1)]


def profile_phase_bars(profile: dict) -> dict:
    base_tf = profile["window"]["templateTf"]
    base_step = TF_MS[base_tf]
    start_ts = int(profile["window"]["startTs"])
    return {
        "initial_to_liquidity": max(
            1,
            round((int(profile["keyCandles"]["liquidity_seeker"]["timestamp"]) - start_ts) / base_step),
        ),
        "initial_to_late_div": max(
            1,
            round((int(profile["keyCandles"]["late_divergence"]["timestamp"]) - start_ts) / base_step),
        ),
        "initial_to_reset": max(
            1,
            round((int(profile["keyCandles"]["reset_candle"]["timestamp"]) - start_ts) / base_step),
        ),
        "initial_to_release": max(
            1,
            round((int(profile["keyCandles"]["release_trigger"]["timestamp"]) - start_ts) / base_step),
        ),
        "pattern_total": max(
            2,
            round((int(profile["window"]["confirmUntilTs"]) - start_ts) / base_step),
        ),
        "late_window_bars": max(1, round((int(profile["scannerBands"]["lateDivWindowMinutes"]) * 60_000) / base_step)),
    }


def load_rows(conn: sqlite3.Connection, tf: str, since_ts: int | None = None, until_ts: int | None = None) -> list[dict]:
    table = f"candles_{tf}"
    cols = (
        "timestamp, open, high, low, close, vol_vs_ma, delta_pct, futures_minus_spot_volume, "
        "spot_delta, futures_delta, futures_minus_spot_delta, "
        "bos_bull, bos_bear, choch, bull_div, bear_div, bull_div_streak, bear_div_streak, "
        "is_seeker_hs, is_seeker_ls, is_seeker_div_hs, is_seeker_div_ls, seeker_div_nr, "
        "is_seeker_kill, killed_seekers_count, cluster_range_atr, cluster_spread, "
        "break_depth, sw_bullish, same_dir, body_ratio, wick_ratio, body_position, "
        "seeker_zone_top, seeker_zone_bottom, seeker_zone_size, seeker_zone_vs_body, "
        "seeker_wick_dominance, dist_swing_high, dist_swing_low"
        ", whale_sentiment, whale_confidence, bull_pressure, bear_pressure, whale_cluster, "
        "whale_cluster_strength, whale_cluster_dir, elite_whale_active"
    )
    where = []
    params: list[int] = []
    if since_ts is not None:
        where.append("timestamp >= ?")
        params.append(since_ts)
    if until_ts is not None:
        where.append("timestamp <= ?")
        params.append(until_ts)
    sql = f"SELECT {cols} FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY timestamp"
    cur = conn.execute(sql, tuple(params))
    return [dict(row) for row in cur.fetchall()]


def find_m1_bursts(rows: list[dict], direction: str, delta_min: float) -> tuple[int, list[int]]:
    ts_list: list[int] = []
    if direction == "long":
        for row in rows:
            if float(row.get("delta_pct") or 0.0) >= delta_min:
                ts_list.append(int(row["timestamp"]))
    else:
        for row in rows:
            if float(row.get("delta_pct") or 0.0) <= -delta_min:
                ts_list.append(int(row["timestamp"]))
    return len(ts_list), ts_list


def evaluate_trigger_context(rows: list[dict], direction: str) -> tuple[bool, float, dict]:
    if not rows:
        return False, 0.0, {"reason": "no_rows"}

    if direction == "long":
        bos_count = sum(int(bool(row.get("bos_bull"))) for row in rows)
        opposite_bos_count = sum(int(bool(row.get("bos_bear"))) for row in rows)
        seeker_div_count = sum(int(bool(row.get("is_seeker_div_hs"))) for row in rows)
        directional_close_count = sum(
            int(float(row.get("close") or 0.0) > float(row.get("open") or 0.0))
            for row in rows
        )
        lead_count = sum(int(float(row.get("futures_minus_spot_volume") or 0.0) > 0.0) for row in rows)
        futures_delta_count = sum(int(float(row.get("futures_delta") or 0.0) > 0.0) for row in rows)
        whale_support_count = sum(
            int(
                row.get("whale_sentiment") is not None
                and float(row.get("whale_sentiment") or 0.0) >= 0.0
                and float(row.get("bull_pressure") or 0.0) >= float(row.get("bear_pressure") or 0.0)
            )
            for row in rows
        )
    else:
        bos_count = sum(int(bool(row.get("bos_bear"))) for row in rows)
        opposite_bos_count = sum(int(bool(row.get("bos_bull"))) for row in rows)
        seeker_div_count = sum(int(bool(row.get("is_seeker_div_ls"))) for row in rows)
        directional_close_count = sum(
            int(float(row.get("close") or 0.0) < float(row.get("open") or 0.0))
            for row in rows
        )
        lead_count = sum(int(float(row.get("futures_minus_spot_volume") or 0.0) < 0.0) for row in rows)
        futures_delta_count = sum(int(float(row.get("futures_delta") or 0.0) < 0.0) for row in rows)
        whale_support_count = sum(
            int(
                row.get("whale_sentiment") is not None
                and float(row.get("whale_sentiment") or 0.0) <= 0.0
                and float(row.get("bear_pressure") or 0.0) >= float(row.get("bull_pressure") or 0.0)
            )
            for row in rows
        )

    choch_count = sum(int(bool(row.get("choch"))) for row in rows)
    seeker_kill_count = sum(int(bool(row.get("is_seeker_kill"))) for row in rows)
    bullish_shape_count = sum(int(bool(row.get("sw_bullish"))) for row in rows)
    avg_lead_volume = sum(float(row.get("futures_minus_spot_volume") or 0.0) for row in rows) / len(rows)
    avg_futures_delta = sum(float(row.get("futures_delta") or 0.0) for row in rows) / len(rows)
    avg_whale_sentiment = None
    whale_values = [float(row["whale_sentiment"]) for row in rows if row.get("whale_sentiment") is not None]
    if whale_values:
        avg_whale_sentiment = sum(whale_values) / len(whale_values)

    score = 0.0
    score += 0.20 * clamp(bos_count / 2.0, 0.0, 1.0)
    score += 0.15 * clamp(choch_count / 1.0, 0.0, 1.0)
    score += 0.15 * clamp(seeker_kill_count / 2.0, 0.0, 1.0)
    score += 0.10 * clamp(seeker_div_count / 2.0, 0.0, 1.0)
    score += 0.10 * clamp(directional_close_count / max(1.0, len(rows) * 0.5), 0.0, 1.0)
    score += 0.10 * clamp(lead_count / max(1.0, len(rows) * 0.4), 0.0, 1.0)
    score += 0.10 * clamp(futures_delta_count / max(1.0, len(rows) * 0.4), 0.0, 1.0)
    score += 0.10 * clamp(whale_support_count / max(1.0, len(rows) * 0.25), 0.0, 1.0)

    context_ok = bos_count > 0 or choch_count > 0 or seeker_kill_count > 0 or lead_count >= 2
    return context_ok, score, {
        "rows": len(rows),
        "bosCount": bos_count,
        "oppositeBosCount": opposite_bos_count,
        "chochCount": choch_count,
        "seekerKillCount": seeker_kill_count,
        "seekerDivCount": seeker_div_count,
        "directionalCloseCount": directional_close_count,
        "bullishShapeCount": bullish_shape_count,
        "leadCount": lead_count,
        "futuresDeltaCount": futures_delta_count,
        "whaleSupportCount": whale_support_count,
        "avgLeadVolume": round(avg_lead_volume, 4),
        "avgFuturesDelta": round(avg_futures_delta, 4),
        "avgWhaleSentiment": round(avg_whale_sentiment, 4) if avg_whale_sentiment is not None else None,
        "firstTs": int(rows[0]["timestamp"]),
        "lastTs": int(rows[-1]["timestamp"]),
    }


def matches_breakout(row: dict, direction: str, bands: dict) -> tuple[bool, float, dict]:
    vol = float(row.get("vol_vs_ma") or 0.0)
    fsm = float(row.get("futures_minus_spot_volume") or 0.0)
    fut_delta = float(row.get("futures_delta") or 0.0)
    lead_delta = float(row.get("futures_minus_spot_delta") or 0.0)
    whale_sent = row.get("whale_sentiment")
    bull_pressure = float(row.get("bull_pressure") or 0.0)
    bear_pressure = float(row.get("bear_pressure") or 0.0)
    if direction == "long":
        structure_ok = bool(row.get("bos_bull")) and float(row["close"]) > float(row["open"])
        flow_ok = fsm >= float(bands["templateBreakoutFuturesMinusSpotMin"])
        futures_ok = fut_delta >= float(bands.get("templateBreakoutFuturesDeltaMin") or 0.0)
        whale_ok = whale_sent is None or (float(whale_sent) >= 0.0 and bull_pressure >= bear_pressure)
    else:
        structure_ok = bool(row.get("bos_bear")) and float(row["close"]) < float(row["open"])
        flow_ok = fsm <= -float(bands["templateBreakoutFuturesMinusSpotMin"])
        futures_ok = fut_delta <= -float(bands.get("templateBreakoutFuturesDeltaMin") or 0.0)
        whale_ok = whale_sent is None or (float(whale_sent) <= 0.0 and bear_pressure >= bull_pressure)
    vol_ok = float(bands["templateBreakoutVolVsMa"][0]) <= vol <= float(bands["templateBreakoutVolVsMa"][1])
    score = 0.0
    if structure_ok:
        score += 0.4
    if flow_ok:
        score += 0.2
    if futures_ok:
        score += 0.15
    if vol_ok:
        score += 0.15
    if whale_ok:
        score += 0.1
    return structure_ok and flow_ok and futures_ok and vol_ok, score, {
        "structureOk": structure_ok,
        "flowOk": flow_ok,
        "futuresOk": futures_ok,
        "volOk": vol_ok,
        "whaleOk": whale_ok,
        "volVsMa": round(vol, 4),
        "leadVolume": round(fsm, 4),
        "futuresDelta": round(fut_delta, 4),
        "leadDelta": round(lead_delta, 4),
        "whaleSentiment": round(float(whale_sent), 4) if whale_sent is not None else None,
        "bullPressure": round(bull_pressure, 4),
        "bearPressure": round(bear_pressure, 4),
    }


def find_liquidity_push(rows: list[dict], direction: str, bands: dict) -> tuple[dict | None, float]:
    best_row = None
    best_score = 0.0
    for row in rows:
        if direction == "long":
            seeker_ok = bool(row.get("is_seeker_hs"))
        else:
            seeker_ok = bool(row.get("is_seeker_ls"))
        if not seeker_ok:
            continue
        zone_size = float(row.get("seeker_zone_size") or 0.0)
        zone_vs_body = float(row.get("seeker_zone_vs_body") or 0.0)
        score = 0.0
        if zone_size >= float(bands["seekerZoneSizeMin"]):
            score += 0.5
        else:
            score += 0.5 * clamp(zone_size / max(1.0, float(bands["seekerZoneSizeMin"])), 0.0, 1.0)
        if zone_vs_body >= float(bands["seekerZoneVsBodyMin"]):
            score += 0.5
        else:
            score += 0.5 * clamp(zone_vs_body / max(1.0, float(bands["seekerZoneVsBodyMin"])), 0.0, 1.0)
        if score > best_score:
            best_score = score
            best_row = row
    return best_row, best_score


def evaluate_hold(initial: dict, later_rows: list[dict], direction: str) -> tuple[bool, float, dict]:
    if direction == "long":
        breakout_level = float(initial["low"])
        later_extreme = min(float(row["low"]) for row in later_rows) if later_rows else breakout_level
        held = later_extreme > breakout_level
    else:
        breakout_level = float(initial["high"])
        later_extreme = max(float(row["high"]) for row in later_rows) if later_rows else breakout_level
        held = later_extreme < breakout_level
    score = 1.0 if held else 0.0
    return held, score, {
        "breakoutLevel": breakout_level,
        "laterExtreme": later_extreme,
        "held": held,
    }


def evaluate_compression(rows: list[dict], direction: str, bands: dict) -> tuple[bool, float, dict]:
    if not rows:
        return False, 0.0, {"reason": "no_rows"}
    vols = [float(row.get("vol_vs_ma") or 0.0) for row in rows]
    median_vol = median(vols)
    avg_vol = sum(vols) / len(vols)
    avg_lead = sum(float(row.get("futures_minus_spot_volume") or 0.0) for row in rows) / len(rows)
    if direction == "long":
        div_count = sum(int(bool(row.get("is_seeker_div_hs"))) for row in rows)
    else:
        div_count = sum(int(bool(row.get("is_seeker_div_ls"))) for row in rows)
    median_ok = median_vol <= float(bands["compressionMedianVolVsMaMax"])
    div_ok = div_count >= int(bands["compressionHsDivCountMin"])
    lead_ok = (
        avg_lead >= float(bands.get("compressionLeadVolumeMin") or 0.0)
        if direction == "long"
        else avg_lead <= -float(bands.get("compressionLeadVolumeMin") or 0.0)
    )
    score = 0.0
    score += 0.4 * clamp((float(bands["compressionMedianVolVsMaMax"]) - median_vol) / max(0.05, float(bands["compressionMedianVolVsMaMax"])), 0.0, 1.0)
    score += 0.4 * clamp(div_count / max(1, int(bands["compressionHsDivCountMin"])), 0.0, 1.0)
    score += 0.2 * (1.0 if lead_ok else 0.0)
    return median_ok and div_ok, score, {
        "medianVolVsMa": round(median_vol, 4),
        "avgVolVsMa": round(avg_vol, 4),
        "divCount": div_count,
        "avgLeadVolume": round(avg_lead, 4),
        "leadOk": lead_ok,
    }


def evaluate_late_pressure(rows: list[dict], direction: str) -> tuple[bool, float, dict]:
    if not rows:
        return False, 0.0, {"reason": "no_rows"}
    pressure_rows = []
    for row in rows:
        if direction == "long":
            if bool(row.get("is_seeker_div_hs")) or bool(row.get("bear_div")):
                pressure_rows.append(row)
        else:
            if bool(row.get("is_seeker_div_ls")) or bool(row.get("bull_div")):
                pressure_rows.append(row)
    score = clamp(len(pressure_rows) / 2.0, 0.0, 1.0)
    return len(pressure_rows) > 0, score, {
        "count": len(pressure_rows),
        "timestamps": [int(row["timestamp"]) for row in pressure_rows],
    }


def evaluate_reset_and_release(reset_row: dict | None, release_rows: list[dict], direction: str, bands: dict) -> tuple[bool, float, dict]:
    if reset_row is None or not release_rows:
        return False, 0.0, {"reason": "missing_rows"}
    if direction == "long":
        reset_ok = float(reset_row["close"]) <= float(reset_row["open"])
    else:
        reset_ok = float(reset_row["close"]) >= float(reset_row["open"])

    best_release = None
    best_score = 0.0
    for row in release_rows:
        vol = float(row.get("vol_vs_ma") or 0.0)
        delta = float(row.get("delta_pct") or 0.0)
        fsm = float(row.get("futures_minus_spot_volume") or 0.0)
        fut_delta = float(row.get("futures_delta") or 0.0)
        whale_sent = row.get("whale_sentiment")
        bull_pressure = float(row.get("bull_pressure") or 0.0)
        bear_pressure = float(row.get("bear_pressure") or 0.0)
        if direction == "long":
            release_struct_ok = bool(row.get("bos_bull")) or bool(row.get("sw_bullish")) or float(row["close"]) > float(row["open"])
            delta_ok = delta >= float(bands["releaseDeltaPctMin"])
            flow_ok = fsm >= float(bands["releaseFuturesMinusSpotMin"])
            futures_ok = fut_delta >= float(bands.get("releaseFuturesDeltaMin") or 0.0)
            whale_ok = bands.get("releaseWhaleSentimentMin") is None or (
                whale_sent is not None and float(whale_sent) >= float(bands["releaseWhaleSentimentMin"]) and bull_pressure >= bear_pressure
            )
        else:
            release_struct_ok = bool(row.get("bos_bear")) or (not bool(row.get("sw_bullish"))) or float(row["close"]) < float(row["open"])
            delta_ok = delta <= -float(bands["releaseDeltaPctMin"])
            flow_ok = fsm <= -float(bands["releaseFuturesMinusSpotMin"])
            futures_ok = fut_delta <= -float(bands.get("releaseFuturesDeltaMin") or 0.0)
            whale_ok = bands.get("releaseWhaleSentimentMin") is None or (
                whale_sent is not None and float(whale_sent) <= -float(bands["releaseWhaleSentimentMin"]) and bear_pressure >= bull_pressure
            )
        vol_ok = vol >= float(bands["releaseVolVsMaMin"])
        score = 0.0
        if release_struct_ok:
            score += 0.3
        if vol_ok:
            score += 0.15
        if delta_ok:
            score += 0.15
        if flow_ok:
            score += 0.15
        if futures_ok:
            score += 0.15
        if whale_ok:
            score += 0.1
        if score > best_score:
            best_score = score
            best_release = {
                "timestamp": int(row["timestamp"]),
                "volVsMa": round(vol, 4),
                "deltaPct": round(delta, 4),
                "leadVolume": round(fsm, 4),
                "futuresDelta": round(fut_delta, 4),
                "whaleSentiment": round(float(whale_sent), 4) if whale_sent is not None else None,
                "flags": {
                    "bosBull": bool(row.get("bos_bull")),
                    "bosBear": bool(row.get("bos_bear")),
                    "swBullish": bool(row.get("sw_bullish")),
                },
            }
    ok = reset_ok and best_score >= 0.75
    return ok, 0.4 * (1.0 if reset_ok else 0.0) + 0.6 * best_score, {
        "resetOk": reset_ok,
        "bestRelease": best_release,
    }


def scan_pattern(
    conn: sqlite3.Connection,
    profile: dict,
    direction: str,
    since_ts: int | None,
    until_ts: int | None,
    top_n: int,
    min_score: float,
    template_tf: str | None = None,
    trigger_tf: str | None = None,
    include_all: bool = False,
) -> dict:
    template_tf = template_tf or profile["window"]["templateTf"]
    trigger_tf = trigger_tf or lower_tf(template_tf)
    bands = profile["scannerBands"]
    phase_bars = profile_phase_bars(profile)

    template_rows = load_rows(conn, template_tf, since_ts, until_ts)
    trigger_rows = load_rows(conn, trigger_tf, since_ts, until_ts)
    m1_rows_all = trigger_rows if template_tf == "1m" else load_rows(conn, "1m", since_ts, until_ts)
    template_step_ms = TF_MS[template_tf]
    liquidity_offset_ts = phase_bars["initial_to_liquidity"] * template_step_ms
    late_div_offset_ts = phase_bars["initial_to_late_div"] * template_step_ms
    reset_offset_ts = phase_bars["initial_to_reset"] * template_step_ms
    release_offset_ts = phase_bars["initial_to_release"] * template_step_ms
    late_window_ms = phase_bars["late_window_bars"] * template_step_ms
    liquidity_min_offset = max(template_step_ms, liquidity_offset_ts - template_step_ms)
    liquidity_max_offset = liquidity_offset_ts + 2 * template_step_ms
    pattern_total_ms = phase_bars["pattern_total"] * template_step_ms
    scan_until = until_ts if until_ts is not None else (template_rows[-1]["timestamp"] if template_rows else 0)

    candidates = []
    for i, initial in enumerate(template_rows):
        initial_ts = int(initial["timestamp"])
        if initial_ts + pattern_total_ms > scan_until:
            break

        breakout_ok, breakout_score, breakout_meta = matches_breakout(initial, direction, bands)
        if not breakout_ok:
            continue

        window_end = initial_ts + pattern_total_ms
        full_window = [row for row in template_rows if initial_ts <= int(row["timestamp"]) <= window_end]
        if len(full_window) < 10:
            continue

        post_break_rows = [row for row in full_window if initial_ts + liquidity_min_offset <= int(row["timestamp"]) <= initial_ts + liquidity_max_offset]
        liquidity_row, liquidity_score = find_liquidity_push(post_break_rows, direction, bands)
        if liquidity_row is None:
            continue

        seeker_ts = int(liquidity_row["timestamp"])
        late_div_ts = initial_ts + late_div_offset_ts
        reset_ts = initial_ts + reset_offset_ts
        confirm_release_ts = initial_ts + release_offset_ts
        later_rows = [row for row in full_window if int(row["timestamp"]) > initial_ts]
        hold_ok, hold_score, hold_meta = evaluate_hold(initial, later_rows, direction)
        if not hold_ok:
            continue

        compression_rows = [
            row
            for row in full_window
            if seeker_ts + template_step_ms <= int(row["timestamp"]) <= late_div_ts - template_step_ms
        ]
        compression_ok, compression_score, compression_meta = evaluate_compression(compression_rows, direction, bands)

        late_rows = [
            row for row in full_window
            if late_div_ts - late_window_ms <= int(row["timestamp"]) <= late_div_ts + late_window_ms
        ]
        pressure_ok, pressure_score, pressure_meta = evaluate_late_pressure(late_rows, direction)

        reset_row = next((row for row in full_window if int(row["timestamp"]) == reset_ts), None)
        release_rows = [row for row in full_window if confirm_release_ts - template_step_ms <= int(row["timestamp"]) <= confirm_release_ts + template_step_ms]
        release_ok, release_score, release_meta = evaluate_reset_and_release(reset_row, release_rows, direction, bands)

        trigger_rows_window = [
            row
            for row in m1_rows_all
            if reset_ts <= int(row["timestamp"]) <= confirm_release_ts
        ]
        burst_count, burst_ts = find_m1_bursts(trigger_rows_window, direction, float(bands["releaseDeltaPctMin"]))
        trigger_ok, trigger_score, trigger_meta = evaluate_trigger_context(trigger_rows_window, direction)
        burst_needed = int(bands["m1DeltaBurstCountMin"])
        burst_score = clamp(burst_count / max(1, burst_needed), 0.0, 1.0)
        m1_score = 0.45 * burst_score + 0.55 * trigger_score

        score = round(
            100.0
            * (
                0.20 * breakout_score
                + 0.15 * liquidity_score
                + 0.15 * hold_score
                + 0.15 * compression_score
                + 0.10 * pressure_score
                + 0.15 * release_score
                + 0.10 * m1_score
            ),
            2,
        )
        if score < min_score:
            continue
        if not (compression_ok or pressure_ok or release_ok):
            continue
        candidates.append(
            {
                "direction": direction,
                "templateTf": template_tf,
                "triggerTf": trigger_tf,
                "score": score,
                "initialTs": initial_ts,
                "windowEndTs": window_end,
                "initialTime": fmt_ts(initial_ts),
                "windowEnd": fmt_ts(window_end),
                "keyTimes": {
                    "liquiditySeeker": fmt_ts(seeker_ts),
                    "reset": fmt_ts(reset_ts),
                    "release": fmt_ts(confirm_release_ts),
                },
                "components": {
                    "breakout": round(breakout_score, 4),
                    "liquidityPush": round(liquidity_score, 4),
                    "hold": round(hold_score, 4),
                    "compression": round(compression_score, 4),
                    "latePressure": round(pressure_score, 4),
                    "release": round(release_score, 4),
                    "m1Confirm": round(m1_score, 4),
                },
                "evidence": {
                    "stageFlags": {
                        "breakoutOk": breakout_ok,
                        "liquidityOk": liquidity_row is not None,
                        "holdOk": hold_ok,
                        "compressionOk": compression_ok,
                        "latePressureOk": pressure_ok,
                        "releaseOk": release_ok,
                        "m1ConfirmOk": burst_count >= burst_needed or trigger_ok,
                    },
                    "breakout": breakout_meta,
                    "hold": hold_meta,
                    "compression": compression_meta,
                    "latePressure": pressure_meta,
                    "release": release_meta,
                    "m1Bursts": {
                        "count": burst_count,
                        "needed": burst_needed,
                        "score": round(burst_score, 4),
                        "timestamps": [fmt_ts(ts) for ts in burst_ts],
                    },
                    "m1TriggerContext": {
                        "ok": trigger_ok,
                        "score": round(trigger_score, 4),
                        "tf": "1m" if template_tf != "1m" else trigger_tf,
                        **trigger_meta,
                    },
                },
            }
        )

    candidates.sort(key=lambda item: (-float(item["score"]), int(item["initialTs"])))
    payload = {
        "direction": direction,
        "templateTf": template_tf,
        "triggerTf": trigger_tf,
        "count": len(candidates),
        "topMatches": candidates[:top_n],
    }
    if include_all:
        payload["allMatches"] = candidates
    return payload


def scan_all_timeframes(
    conn: sqlite3.Connection,
    profile: dict,
    since_ts: int | None,
    until_ts: int | None,
    top_n: int,
    min_score: float,
    include_all: bool = False,
) -> dict:
    by_tf = {}
    for template_tf in TF_ORDER:
        trigger_tf = lower_tf(template_tf)
        by_tf[template_tf] = {
            "long": scan_pattern(conn, profile, "long", since_ts, until_ts, top_n, min_score, template_tf=template_tf, trigger_tf=trigger_tf, include_all=include_all),
            "short": scan_pattern(conn, profile, "short", since_ts, until_ts, top_n, min_score, template_tf=template_tf, trigger_tf=trigger_tf, include_all=include_all),
        }
    return by_tf


def render_markdown(profile_label: str, results: dict) -> str:
    lines = [
        f"# Pattern Scanner v1 · {profile_label}",
        "",
    ]
    for direction in ("long", "short"):
        block = results[direction]
        lines.extend(
            [
                f"## {direction.upper()}",
                "",
                f"- Matches: `{block['count']}`",
                "",
            ]
        )
        for item in block["topMatches"]:
            lines.extend(
                [
                    f"### {item['initialTime']} · score `{item['score']}`",
                    f"- liquidity seeker: `{item['keyTimes']['liquiditySeeker']}`",
                    f"- reset: `{item['keyTimes']['reset']}`",
                    f"- release: `{item['keyTimes']['release']}`",
                    f"- components: `{item['components']}`",
                    "",
                ]
            )
    return "\n".join(lines) + "\n"


def render_all_tf_markdown(profile_label: str, by_tf: dict) -> str:
    lines = [f"# Pattern Scanner v1 · All Timeframes · {profile_label}", ""]
    for tf in TF_ORDER:
        block = by_tf[tf]
        lines.extend(
            [
                f"## {tf}",
                "",
                f"- Long matches: `{block['long']['count']}`",
                f"- Short matches: `{block['short']['count']}`",
                "",
            ]
        )
        for side in ("long", "short"):
            top = block[side]["topMatches"][:3]
            if not top:
                continue
            lines.append(f"### top {side}")
            for item in top:
                lines.append(f"- `{item['initialTime']}` score `{item['score']}` trigger `{item['triggerTf']}`")
            lines.append("")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan history for tolerant reference-pattern matches.")
    parser.add_argument("--profile", required=True, help="Path to reference pattern profile JSON")
    parser.add_argument("--since", help="Berlin local time, format YYYY-MM-DD HH:MM")
    parser.add_argument("--until", help="Berlin local time, format YYYY-MM-DD HH:MM")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--min-score", type=float, default=62.0)
    parser.add_argument("--output-prefix", default="pattern_scanner_v1")
    parser.add_argument("--all-timeframes", action="store_true")
    return parser.parse_args()


def parse_optional_local_ts(value: str | None) -> int | None:
    if not value:
        return None
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=BERLIN).timestamp() * 1000)


def main() -> None:
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    profile = load_json(Path(args.profile))
    since_ts = parse_optional_local_ts(args.since)
    until_ts = parse_optional_local_ts(args.until)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    results = {
        "profileLabel": profile["label"],
        "profileWindow": profile["window"],
        "long": scan_pattern(conn, profile, "long", since_ts, until_ts, args.top_n, args.min_score),
        "short": scan_pattern(conn, profile, "short", since_ts, until_ts, args.top_n, args.min_score),
    }

    if args.all_timeframes:
        results["allTimeframes"] = scan_all_timeframes(conn, profile, since_ts, until_ts, args.top_n, args.min_score)

    slug = args.output_prefix
    json_path = OUT_DIR / f"{slug}.json"
    md_path = OUT_DIR / f"{slug}.md"
    json_path.write_text(json.dumps(results, indent=2))
    if args.all_timeframes:
        md_path.write_text(render_all_tf_markdown(profile["label"], results["allTimeframes"]))
    else:
        md_path.write_text(render_markdown(profile["label"], results))
    print(json_path)
    print(md_path)


if __name__ == "__main__":
    main()
