#!/usr/bin/env python3
"""
Build a reusable reference pattern profile from an Edgerunner window.

The goal is not to create a rigid setup recipe. It extracts:
- key candles
- phase summaries
- hard vs soft features
- mirrored bullish/bearish interpretation
- tolerance bands for a future scanner
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo


DB_PATH = Path("/home/axe240/Projects/edgerunner_restored/edgerunner.db")
OUT_DIR = Path("/home/axe240/Projects/edgerunner_restored/.runtime")
BERLIN = ZoneInfo("Europe/Berlin")

RESEARCH_TFS = ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]

FEATURE_COLUMNS = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "total_range",
    "body_ratio",
    "wick_ratio",
    "body_position",
    "delta_pct",
    "vol_vs_ma",
    "cluster_range_atr",
    "cluster_spread",
    "bos_bull",
    "bos_bear",
    "choch",
    "bull_div",
    "bear_div",
    "bull_div_streak",
    "bear_div_streak",
    "is_seeker_hs",
    "is_seeker_ls",
    "is_seeker_div_hs",
    "is_seeker_div_ls",
    "seeker_div_nr",
    "is_seeker_kill",
    "killed_seekers_count",
    "killed_seeker_divs",
    "killed_seekers_age_min",
    "killed_seekers_age_max",
    "break_depth",
    "swing_age",
    "sw_bullish",
    "same_dir",
    "bos_body",
    "bos_wick",
    "delta_vs_ma",
    "spot_volume",
    "spot_delta",
    "futures_volume",
    "futures_delta",
    "futures_minus_spot_volume",
    "futures_minus_spot_delta",
    "dist_swing_high",
    "dist_swing_low",
    "seeker_zone_top",
    "seeker_zone_bottom",
    "seeker_zone_size",
    "seeker_zone_vs_body",
    "seeker_wick_dominance",
    "htf_trend",
    "htf_bos",
    "whale_sentiment",
    "whale_confidence",
    "bull_pressure",
    "bear_pressure",
    "whale_cluster",
    "whale_cluster_strength",
    "whale_cluster_dir",
    "elite_whale_active",
]


@dataclass
class KeyCandle:
    label: str
    tf: str
    ts: int


def parse_local_ts(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=BERLIN).timestamp() * 1000)


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, BERLIN).strftime("%d.%m.%Y %H:%M")


def load_rows(conn: sqlite3.Connection, tf: str, since_ts: int, until_ts: int) -> list[dict]:
    cols = ", ".join(FEATURE_COLUMNS)
    table = f"candles_{tf}"
    cur = conn.execute(
        f"SELECT {cols} FROM {table} WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp",
        (since_ts, until_ts),
    )
    return [dict(row) for row in cur.fetchall()]


def load_candle(conn: sqlite3.Connection, tf: str, ts: int) -> dict | None:
    cols = ", ".join(FEATURE_COLUMNS)
    table = f"candles_{tf}"
    row = conn.execute(f"SELECT {cols} FROM {table} WHERE timestamp = ?", (ts,)).fetchone()
    return dict(row) if row else None


def load_cycle_context(conn: sqlite3.Connection, tf: str, ts: int) -> dict:
    rows = conn.execute(
        """
        SELECT cycle_id, cycle_type, status, origin_ts, zone_bottom, zone_top, zone_size,
               div_count_total, last_div_ts, last_kill_ts, age_bars, age_ms
        FROM seeker_cycles
        WHERE timeframe = ?
          AND origin_ts <= ?
          AND (last_event_ts IS NULL OR last_event_ts >= ?)
        ORDER BY origin_ts
        """,
        (tf, ts, ts),
    ).fetchall()
    active = [dict(row) for row in rows]
    return {
        "activeCycles": active,
        "openCycles": [row for row in active if row["status"] == "open"],
        "killedCycles": [row for row in active if row["status"] == "killed"],
    }


def feature_flags(row: dict) -> list[str]:
    keys = [
        "bos_bull",
        "bos_bear",
        "choch",
        "bull_div",
        "bear_div",
        "is_seeker_hs",
        "is_seeker_ls",
        "is_seeker_div_hs",
        "is_seeker_div_ls",
        "is_seeker_kill",
        "sw_bullish",
    ]
    return [key for key in keys if row.get(key)]


def summarize_window(rows: list[dict]) -> dict:
    if not rows:
        return {}
    avg_spot_volume = sum(float(r.get("spot_volume") or 0.0) for r in rows) / len(rows)
    avg_futures_volume = sum(float(r.get("futures_volume") or 0.0) for r in rows) / len(rows)
    avg_spot_delta = sum(float(r.get("spot_delta") or 0.0) for r in rows) / len(rows)
    avg_futures_delta = sum(float(r.get("futures_delta") or 0.0) for r in rows) / len(rows)
    avg_lead_volume = sum(float(r.get("futures_minus_spot_volume") or 0.0) for r in rows) / len(rows)
    avg_lead_delta = sum(float(r.get("futures_minus_spot_delta") or 0.0) for r in rows) / len(rows)
    whale_rows = [r for r in rows if r.get("whale_sentiment") is not None]
    return {
        "count": len(rows),
        "firstTs": rows[0]["timestamp"],
        "lastTs": rows[-1]["timestamp"],
        "high": max(float(r["high"]) for r in rows),
        "low": min(float(r["low"]) for r in rows),
        "avgVolVsMa": round(sum(float(r.get("vol_vs_ma") or 0.0) for r in rows) / len(rows), 4),
        "medianVolVsMa": round(median(float(r.get("vol_vs_ma") or 0.0) for r in rows), 4),
        "avgDeltaPct": round(sum(float(r.get("delta_pct") or 0.0) for r in rows) / len(rows), 4),
        "avgSpotVolume": round(avg_spot_volume, 4),
        "avgFuturesVolume": round(avg_futures_volume, 4),
        "avgSpotDelta": round(avg_spot_delta, 4),
        "avgFuturesDelta": round(avg_futures_delta, 4),
        "avgLeadVolume": round(avg_lead_volume, 4),
        "avgLeadDelta": round(avg_lead_delta, 4),
        "maxZoneSize": round(max(float(r.get("seeker_zone_size") or 0.0) for r in rows), 4),
        "whale": {
            "count": len(whale_rows),
            "avgSentiment": round(sum(float(r.get("whale_sentiment") or 0.0) for r in whale_rows) / len(whale_rows), 4) if whale_rows else 0.0,
            "avgClusterStrength": round(sum(float(r.get("whale_cluster_strength") or 0.0) for r in whale_rows) / len(whale_rows), 4) if whale_rows else 0.0,
            "eliteHits": sum(int(bool(r.get("elite_whale_active"))) for r in whale_rows),
        },
        "counts": {
            "bosBull": sum(int(bool(r.get("bos_bull"))) for r in rows),
            "bosBear": sum(int(bool(r.get("bos_bear"))) for r in rows),
            "choch": sum(int(bool(r.get("choch"))) for r in rows),
            "bullDiv": sum(int(bool(r.get("bull_div"))) for r in rows),
            "bearDiv": sum(int(bool(r.get("bear_div"))) for r in rows),
            "hsSeeker": sum(int(bool(r.get("is_seeker_hs"))) for r in rows),
            "lsSeeker": sum(int(bool(r.get("is_seeker_ls"))) for r in rows),
            "hsDiv": sum(int(bool(r.get("is_seeker_div_hs"))) for r in rows),
            "lsDiv": sum(int(bool(r.get("is_seeker_div_ls"))) for r in rows),
            "seekerKill": sum(int(bool(r.get("is_seeker_kill"))) for r in rows),
            "swBullish": sum(int(bool(r.get("sw_bullish"))) for r in rows),
        },
    }


def find_key_candle(key_candles: list[KeyCandle], label: str) -> KeyCandle | None:
    for item in key_candles:
        if item.label == label:
            return item
    return None


def phase_rows(all_rows: list[dict], start_ts: int, end_ts: int) -> list[dict]:
    return [row for row in all_rows if start_ts <= int(row["timestamp"]) <= end_ts]


def build_hard_soft_features(
    template_rows: list[dict],
    trigger_rows: list[dict],
    key_data: dict[str, dict],
) -> tuple[list[dict], list[dict], dict]:
    initial = key_data.get("initial_breakout")
    seeker = key_data.get("liquidity_seeker")
    late_div = key_data.get("late_divergence")
    reset = key_data.get("reset_candle")
    release = key_data.get("release_trigger")

    breakout_low = float(initial["candle"]["low"]) if initial else None
    min_low_after = None
    if breakout_low is not None:
        later = [float(r["low"]) for r in template_rows if int(r["timestamp"]) > int(initial["candle"]["timestamp"])]
        min_low_after = min(later) if later else None

    hs_div_count = sum(int(bool(row.get("is_seeker_div_hs"))) for row in template_rows)
    compression_rows = []
    if seeker and late_div:
        compression_rows = phase_rows(
            template_rows,
            int(seeker["candle"]["timestamp"]) + 15 * 60_000,
            int(late_div["candle"]["timestamp"]) - 15 * 60_000,
        )
    compression_summary = summarize_window(compression_rows) if compression_rows else {}

    trigger_window_rows = []
    if reset and release:
        trigger_window_rows = phase_rows(trigger_rows, int(reset["candle"]["timestamp"]), int(release["candle"]["timestamp"]))
    trigger_delta_bursts = sum(1 for row in trigger_window_rows if float(row.get("delta_pct") or 0.0) >= 0.35)

    hard = []
    if initial:
        hard.append(
            {
                "name": "template_breakout_present",
                "why": "Die Initialkerze auf dem Template-TF ist ein echter bullischer Break mit Struktur und Flow.",
                "evidence": {
                    "bosBull": bool(initial["candle"]["bos_bull"]),
                    "closeAboveOpen": float(initial["candle"]["close"]) > float(initial["candle"]["open"]),
                    "volVsMa": round(float(initial["candle"]["vol_vs_ma"] or 0.0), 4),
                    "spotDelta": round(float(initial["candle"]["spot_delta"] or 0.0), 4),
                    "futuresDelta": round(float(initial["candle"]["futures_delta"] or 0.0), 4),
                    "leadVolume": round(float(initial["candle"]["futures_minus_spot_volume"] or 0.0), 4),
                    "leadDelta": round(float(initial["candle"]["futures_minus_spot_delta"] or 0.0), 4),
                    "whaleSentiment": round(float(initial["candle"]["whale_sentiment"] or 0.0), 4) if initial["candle"].get("whale_sentiment") is not None else None,
                },
            }
        )
    if breakout_low is not None and min_low_after is not None:
        hard.append(
            {
                "name": "hold_above_breakout_low",
                "why": "Der Markt akzeptiert den ersten Break und verliert das Ausgangs-Low später nicht mehr.",
                "evidence": {
                    "breakoutLow": breakout_low,
                    "minLowAfter": min_low_after,
                    "held": min_low_after > breakout_low,
                },
            }
        )
    if seeker:
        hard.append(
            {
                "name": "post_breakout_liquidity_push",
                "why": "Nach dem Initialbreak entsteht ein großer Seeker in einer liquiden Oberseite statt direkter Ablehnung.",
                "evidence": {
                    "isHsSeeker": bool(seeker["candle"]["is_seeker_hs"]),
                    "zoneSize": round(float(seeker["candle"]["seeker_zone_size"] or 0.0), 4),
                    "zoneVsBody": round(float(seeker["candle"]["seeker_zone_vs_body"] or 0.0), 4),
                    "wickDominance": round(float(seeker["candle"]["seeker_wick_dominance"] or 0.0), 4),
                },
            }
        )
    if compression_summary:
        hard.append(
            {
                "name": "compression_after_liquidity_push",
                "why": "Nach dem großen Seeker folgt eine echte Ladephase statt sofortiger Umkehr.",
                "evidence": {
                    "medianVolVsMa": compression_summary["medianVolVsMa"],
                    "avgVolVsMa": compression_summary["avgVolVsMa"],
                    "avgLeadVolume": compression_summary["avgLeadVolume"],
                    "avgLeadDelta": compression_summary["avgLeadDelta"],
                    "hsDivCountInWindow": compression_summary["counts"]["hsDiv"],
                    "rangeLow": compression_summary["low"],
                    "rangeHigh": compression_summary["high"],
                },
            }
        )
    if late_div and reset and release:
        hard.append(
            {
                "name": "late_pressure_reset_then_release",
                "why": "Ein später Druck-/Divergenz-Hinweis wird nicht zum Breakdown, sondern in einen Reset und dann Release übersetzt.",
                "evidence": {
                    "lateDivType": "HS seeker div + MACD bear div",
                    "resetClose": float(reset["candle"]["close"]),
                    "releaseBosBull": bool(release["candle"]["bos_bull"]),
                    "releaseVolVsMa": round(float(release["candle"]["vol_vs_ma"] or 0.0), 4),
                    "releaseDeltaPct": round(float(release["candle"]["delta_pct"] or 0.0), 4),
                    "releaseLeadVolume": round(float(release["candle"]["futures_minus_spot_volume"] or 0.0), 4),
                    "releaseLeadDelta": round(float(release["candle"]["futures_minus_spot_delta"] or 0.0), 4),
                    "releaseWhaleSentiment": round(float(release["candle"]["whale_sentiment"] or 0.0), 4) if release["candle"].get("whale_sentiment") is not None else None,
                    "triggerDeltaBursts": trigger_delta_bursts,
                },
            }
        )

    soft = [
        {
            "name": "old_swing_retest_context",
            "why": "Retest eines älteren Swing-Highs kann helfen, sollte aber nicht als Pflichtkriterium ins universelle Muster.",
        },
        {
            "name": "exact_div_count_not_required",
            "why": "Die genaue Anzahl der HS-Seeker-Divs ist eher ein Intensitätsmerkmal als ein hartes Kriterium.",
        },
        {
            "name": "reset_candle_shape_flexible",
            "why": "Die Reset-Kerze muss nicht exakt rot mit derselben Range sein; wichtig ist der Reset ohne Verlust des Breakout-Bodens.",
        },
    ]

    scanner_bands = {
        "templateBreakoutVolVsMa": [2.0, 4.2],
        "templateBreakoutFuturesMinusSpotMin": 3000.0,
        "templateBreakoutFuturesDeltaMin": max(0.0, round(float(initial["candle"]["futures_delta"] or 0.0) * 0.25, 4)) if initial else 0.0,
        "seekerZoneVsBodyMin": 4.0,
        "seekerZoneSizeMin": 150.0,
        "compressionMedianVolVsMaMax": 0.95,
        "compressionHsDivCountMin": max(3, hs_div_count // 3),
        "compressionLeadVolumeMin": max(0.0, round(float(compression_summary.get("avgLeadVolume") or 0.0) * 0.35, 4)) if compression_summary else 0.0,
        "lateDivWindowMinutes": 30,
        "resetMustHoldBreakoutLow": True,
        "releaseVolVsMaMin": 1.8,
        "releaseDeltaPctMin": 0.25,
        "releaseFuturesMinusSpotMin": 1500.0,
        "releaseFuturesDeltaMin": max(0.0, round(float(release["candle"]["futures_delta"] or 0.0) * 0.25, 4)) if release else 0.0,
        "releaseWhaleSentimentMin": max(0.0, round(float(release["candle"]["whale_sentiment"] or 0.0) * 0.5, 4)) if release and release["candle"].get("whale_sentiment") is not None else None,
        "m1DeltaBurstCountMin": max(2, trigger_delta_bursts),
    }
    return hard, soft, scanner_bands


def build_report(
    conn: sqlite3.Connection,
    label: str,
    direction: str,
    template_tf: str,
    trigger_tf: str,
    since_ts: int,
    until_ts: int,
    confirm_until_ts: int,
    key_candles: list[KeyCandle],
) -> dict:
    rows_by_tf = {tf: load_rows(conn, tf, since_ts, confirm_until_ts) for tf in RESEARCH_TFS}

    key_data: dict[str, dict] = {}
    for item in key_candles:
        candle = load_candle(conn, item.tf, item.ts)
        if not candle:
            continue
        key_data[item.label] = {
            "label": item.label,
            "tf": item.tf,
            "timestamp": item.ts,
            "time": fmt_ts(item.ts),
            "candle": candle,
            "flags": feature_flags(candle),
            "cycleContext": load_cycle_context(conn, item.tf, item.ts),
        }

    template_rows = [row for row in rows_by_tf[template_tf] if since_ts <= int(row["timestamp"]) <= until_ts]
    trigger_rows = [row for row in rows_by_tf[trigger_tf] if since_ts <= int(row["timestamp"]) <= confirm_until_ts]

    phases = []
    phase_specs = [
        ("initial_breakout", since_ts, find_key_candle(key_candles, "liquidity_seeker")),
        ("compression", find_key_candle(key_candles, "liquidity_seeker"), find_key_candle(key_candles, "late_divergence")),
        ("reset", find_key_candle(key_candles, "late_divergence"), find_key_candle(key_candles, "release_trigger")),
        ("release", find_key_candle(key_candles, "release_trigger"), confirm_until_ts),
    ]
    for name, start_ref, end_ref in phase_specs:
        if isinstance(start_ref, KeyCandle):
            start_ts = start_ref.ts
        else:
            start_ts = start_ref
        if isinstance(end_ref, KeyCandle):
            end_ts = end_ref.ts
        else:
            end_ts = end_ref
        if start_ts is None or end_ts is None or end_ts < start_ts:
            continue
        phase_template_rows = phase_rows(template_rows, start_ts, end_ts)
        phase_trigger_rows = phase_rows(trigger_rows, start_ts, end_ts)
        phases.append(
            {
                "name": name,
                "start": fmt_ts(start_ts),
                "end": fmt_ts(end_ts),
                "templateSummary": summarize_window(phase_template_rows),
                "triggerSummary": summarize_window(phase_trigger_rows),
            }
        )

    hard, soft, scanner_bands = build_hard_soft_features(template_rows, trigger_rows, key_data)

    mirror = {
        "patternLogic": "Bullish und bearish werden als dieselbe Zustandslogik gespiegelt behandelt.",
        "mirrorRules": [
            "HS seeker / HS div / obere Liquiditätszone werden für Short-Templates zu LS seeker / LS div / unterer Liquiditätszone gespiegelt.",
            "bullischer Break / Hold über dem Breakout-Low wird zu bärischem Break / Hold unter dem Breakout-High gespiegelt.",
            "positive Futures-vs-Spot-Führung wird für Longs als Vorteil gelesen, negative Führung gespiegelt für Shorts.",
            "M1 confirm bedeutet nicht dieselbe Candleform, sondern denselben Funktionszustand: reclaim / hold / push in Trendrichtung.",
        ],
    }

    timeframe_summaries = {}
    for tf, rows in rows_by_tf.items():
        window_rows = [row for row in rows if since_ts <= int(row["timestamp"]) <= until_ts]
        if not window_rows:
            continue
        timeframe_summaries[tf] = summarize_window(window_rows)

    return {
        "label": label,
        "direction": direction,
        "window": {
            "startTs": since_ts,
            "endTs": until_ts,
            "confirmUntilTs": confirm_until_ts,
            "start": fmt_ts(since_ts),
            "end": fmt_ts(until_ts),
            "confirmUntil": fmt_ts(confirm_until_ts),
            "templateTf": template_tf,
            "triggerTf": trigger_tf,
        },
        "keyCandles": key_data,
        "phases": phases,
        "timeframeSummaries": timeframe_summaries,
        "hardFeatures": hard,
        "softFeatures": soft,
        "scannerBands": scanner_bands,
        "mirror": mirror,
        "notes": {
            "scannerIntent": "Das Template soll toleranzbasiert ähnliche Pre-Breakout-Harnesses finden, nicht nur pixelgenaue Kopien.",
            "overfitWarning": "Alte Swing-Retests und exakte Div-Zahlen werden nur als weicher Kontext geführt.",
        },
    }


def render_markdown(report: dict) -> str:
    lines = [
        f"# {report['label']}",
        "",
        f"- Richtung: `{report['direction']}`",
        f"- Template TF: `{report['window']['templateTf']}`",
        f"- Confirm TF: `{report['window']['triggerTf']}`",
        f"- Fenster: `{report['window']['start']}` -> `{report['window']['end']}`",
        f"- Confirm-Extension: bis `{report['window']['confirmUntil']}`",
        "",
        "## Pattern Read",
        "",
        "Initial breakout -> liquidity push -> hold -> compression -> late pressure reset -> release.",
        "",
        "## Key Candles",
        "",
    ]
    for key, item in report["keyCandles"].items():
        candle = item["candle"]
        lines.extend(
            [
                f"### {key} · {item['tf']} · {item['time']}",
                f"- O/H/L/C: `{candle['open']} / {candle['high']} / {candle['low']} / {candle['close']}`",
                f"- Flags: `{', '.join(item['flags']) if item['flags'] else 'none'}`",
                f"- vol_vs_ma: `{round(float(candle.get('vol_vs_ma') or 0.0), 4)}`",
                f"- delta_pct: `{round(float(candle.get('delta_pct') or 0.0), 4)}`",
                f"- futures_minus_spot: `{round(float(candle.get('futures_minus_spot_volume') or 0.0), 4)}`",
                f"- seeker zone: `{round(float(candle.get('seeker_zone_bottom') or 0.0), 4)} -> {round(float(candle.get('seeker_zone_top') or 0.0), 4)}` size `{round(float(candle.get('seeker_zone_size') or 0.0), 4)}`",
                "",
            ]
        )
    lines.extend(["## Phases", ""])
    for phase in report["phases"]:
        ts = phase["templateSummary"]
        trig = phase["triggerSummary"]
        lines.extend(
            [
                f"### {phase['name']} · {phase['start']} -> {phase['end']}",
                f"- template bars: `{ts.get('count', 0)}` | avg vol `{ts.get('avgVolVsMa', 0)}` | hs div `{ts.get('counts', {}).get('hsDiv', 0)}` | seeker kill `{ts.get('counts', {}).get('seekerKill', 0)}`",
                f"- trigger bars: `{trig.get('count', 0)}` | avg vol `{trig.get('avgVolVsMa', 0)}` | avg delta `{trig.get('avgDeltaPct', 0)}`",
                "",
            ]
        )
    lines.extend(["## Hard Features", ""])
    for item in report["hardFeatures"]:
        lines.append(f"### {item['name']}")
        lines.append(f"- {item['why']}")
        for key, value in item["evidence"].items():
            lines.append(f"- `{key}`: `{value}`")
        lines.append("")
    lines.extend(["## Soft Features", ""])
    for item in report["softFeatures"]:
        lines.append(f"- `{item['name']}`: {item['why']}")
    lines.extend(["", "## Scanner Bands", ""])
    for key, value in report["scannerBands"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Mirrored Version", ""])
    for item in report["mirror"]["mirrorRules"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a reference pattern profile from Edgerunner candles.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--direction", choices=("long", "short"), required=True)
    parser.add_argument("--template-tf", default="15m")
    parser.add_argument("--trigger-tf", default="1m")
    parser.add_argument("--start", required=True, help="Berlin local time, format YYYY-MM-DD HH:MM")
    parser.add_argument("--end", required=True, help="Berlin local time, format YYYY-MM-DD HH:MM")
    parser.add_argument("--confirm-end", required=True, help="Berlin local time, format YYYY-MM-DD HH:MM")
    parser.add_argument(
        "--key",
        action="append",
        default=[],
        help="Key candle in the form label=tf@YYYY-MM-DD HH:MM",
    )
    parser.add_argument("--output-prefix", default="reference_pattern_profile")
    return parser.parse_args()


def parse_key_items(raw_items: list[str]) -> list[KeyCandle]:
    key_candles = []
    for item in raw_items:
        label, spec = item.split("=", 1)
        tf, local_time = spec.split("@", 1)
        key_candles.append(KeyCandle(label=label, tf=tf, ts=parse_local_ts(local_time)))
    return key_candles


def main() -> None:
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    since_ts = parse_local_ts(args.start)
    until_ts = parse_local_ts(args.end)
    confirm_until_ts = parse_local_ts(args.confirm_end)
    key_candles = parse_key_items(args.key)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    report = build_report(
        conn=conn,
        label=args.label,
        direction=args.direction,
        template_tf=args.template_tf,
        trigger_tf=args.trigger_tf,
        since_ts=since_ts,
        until_ts=until_ts,
        confirm_until_ts=confirm_until_ts,
        key_candles=key_candles,
    )

    slug = args.output_prefix
    json_path = OUT_DIR / f"{slug}.json"
    md_path = OUT_DIR / f"{slug}.md"
    json_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(render_markdown(report))

    print(json_path)
    print(md_path)


if __name__ == "__main__":
    main()
