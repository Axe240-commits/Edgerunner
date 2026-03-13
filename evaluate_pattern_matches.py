#!/usr/bin/env python3
"""
Evaluate reference-pattern scanner matches against forward outcomes on 1m data.

This treats scanner matches as historical candidates and answers:
- did the harness actually break out?
- which entry / stop / target model worked best?
- did strong M1 confirm scores separate winners from losers?
"""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from research_fakeout_reclaim import load_timeframe_rows, to_num
from scan_reference_pattern import DB_PATH, load_json, scan_all_timeframes


OUT_DIR = Path("/home/axe240/Projects/edgerunner_restored/.runtime")
BERLIN = ZoneInfo("Europe/Berlin")
TARGET_MODELS = {"1.5R": 1.5, "2.0R": 2.0}
BASE_ENTRY_MODELS = ("close", "retrace_40", "retrace_50", "first_m1_confirm")
STOP_MODELS = ("half_candle", "full_candle", "structure", "reset_candle", "liquidity_zone")


def parse_local_ts(value: str) -> int:
    return int(datetime.strptime(value, "%d.%m.%Y %H:%M").replace(tzinfo=BERLIN).timestamp() * 1000)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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


def find_first_m1_confirm_index(rows_1m: list[dict], start_index: int, direction: str, search_bars: int = 20) -> int | None:
    end = min(len(rows_1m), start_index + 1 + search_bars)
    for idx in range(start_index, end):
        row = rows_1m[idx]
        if direction == "long":
            score = 0
            score += int(bool(row.get("bos_bull")))
            score += int(bool(row.get("choch")))
            score += int(bool(row.get("is_seeker_kill")))
            score += int(float(row.get("close") or 0.0) > float(row.get("open") or 0.0))
            score += int(float(row.get("futures_minus_spot_volume") or 0.0) > 0.0)
            score += int(float(row.get("futures_delta") or 0.0) > 0.0)
        else:
            score = 0
            score += int(bool(row.get("bos_bear")))
            score += int(bool(row.get("choch")))
            score += int(bool(row.get("is_seeker_kill")))
            score += int(float(row.get("close") or 0.0) < float(row.get("open") or 0.0))
            score += int(float(row.get("futures_minus_spot_volume") or 0.0) < 0.0)
            score += int(float(row.get("futures_delta") or 0.0) < 0.0)
        if score >= 3:
            return idx
    return None


def evaluate_trade_from_entry_row(
    rows_1m: list[dict],
    entry_index: int,
    direction: str,
    signal_high: float,
    signal_low: float,
    signal_close: float,
    stop_model: str,
    target_model: str,
    reset_row: dict | None = None,
    liquidity_row: dict | None = None,
    horizon_bars: int = 90,
) -> dict[str, object]:
    entry_row = rows_1m[entry_index]
    signal_range = max(0.5, signal_high - signal_low)
    entry_price = signal_close

    if stop_model == "half_candle":
        risk = signal_range * 0.5
        stop_price = entry_price - risk if direction == "long" else entry_price + risk
    elif stop_model == "full_candle":
        risk = signal_range
        stop_price = entry_price - risk if direction == "long" else entry_price + risk
    elif stop_model == "structure":
        structure = find_recent_structure_stop_from_rows(rows_1m, entry_index, direction)
        if structure is None:
            return {"filled": False, "result": "no_structure_stop"}
        stop_price = structure
        risk = entry_price - stop_price if direction == "long" else stop_price - entry_price
        if risk <= 0:
            return {"filled": False, "result": "invalid_structure_stop"}
    elif stop_model == "reset_candle":
        if reset_row is None:
            return {"filled": False, "result": "no_reset_row"}
        stop_price = to_num(reset_row["low"]) if direction == "long" else to_num(reset_row["high"])
        risk = entry_price - stop_price if direction == "long" else stop_price - entry_price
        if risk <= 0:
            return {"filled": False, "result": "invalid_reset_stop"}
    else:
        if liquidity_row is None:
            return {"filled": False, "result": "no_liquidity_row"}
        if direction == "long":
            stop_price = min(to_num(liquidity_row.get("seeker_zone_bottom")), to_num(liquidity_row.get("low")))
        else:
            stop_price = max(to_num(liquidity_row.get("seeker_zone_top")), to_num(liquidity_row.get("high")))
        risk = entry_price - stop_price if direction == "long" else stop_price - entry_price
        if risk <= 0:
            return {"filled": False, "result": "invalid_liquidity_stop"}

    target_r = TARGET_MODELS[target_model]
    target_price = entry_price + risk * target_r if direction == "long" else entry_price - risk * target_r

    future = rows_1m[entry_index + 1 : entry_index + 1 + horizon_bars]
    if not future:
        return {"filled": False, "result": "no_future"}

    mae_r = 0.0
    mfe_r = 0.0
    stop_hit_index = None
    target_hit_index = None

    for rel_idx, row in enumerate(future, start=1):
        high = to_num(row["high"])
        low = to_num(row["low"])

        if direction == "long":
            adverse = max(0.0, entry_price - low)
            favorable = max(0.0, high - entry_price)
            stop_hit = low <= stop_price
            target_hit = high >= target_price
        else:
            adverse = max(0.0, high - entry_price)
            favorable = max(0.0, entry_price - low)
            stop_hit = high >= stop_price
            target_hit = low <= target_price

        mae_r = max(mae_r, adverse / risk)
        mfe_r = max(mfe_r, favorable / risk)

        if stop_hit and target_hit:
            stop_hit_index = rel_idx
            break
        if stop_hit:
            stop_hit_index = rel_idx
            break
        if target_hit:
            target_hit_index = rel_idx
            break

    result = "timed_out"
    pnl_r = 0.0
    time_to_target = None
    time_to_stop = None
    if target_hit_index is not None:
        result = "clean_run" if mae_r <= 0.25 else "reclaimed_run"
        pnl_r = target_r
        time_to_target = target_hit_index
    elif stop_hit_index is not None:
        result = "stopped"
        pnl_r = -1.0
        time_to_stop = stop_hit_index
    else:
        final_close = to_num(future[-1]["close"])
        pnl_r = ((final_close - entry_price) / risk) if direction == "long" else ((entry_price - final_close) / risk)

    flip_run_after_stop = False
    if stop_hit_index is not None:
        stop_fill = stop_price
        opp_target = stop_fill + risk * 1.5 if direction == "short" else stop_fill - risk * 1.5
        for row in future[stop_hit_index:]:
            high = to_num(row["high"])
            low = to_num(row["low"])
            if direction == "short" and high >= opp_target:
                flip_run_after_stop = True
                break
            if direction == "long" and low <= opp_target:
                flip_run_after_stop = True
                break

    label = {
        "clean_run": "successful_breakout",
        "reclaimed_run": "small_fakeout_then_run",
        "stopped": "failed_breakout",
        "timed_out": "chop_or_no_edge",
    }[result]

    return {
        "filled": True,
        "result": result,
        "label": label,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "risk_pct": risk / entry_price * 100.0,
        "mae_r": mae_r,
        "mfe_r": mfe_r,
        "pnl_r": pnl_r,
        "time_to_target_bars": time_to_target,
        "time_to_stop_bars": time_to_stop,
        "flip_run_after_stop": flip_run_after_stop,
    }


def summarize_records(records: list[dict]) -> dict:
    filled = [r for r in records if r.get("filled")]
    if not filled:
        return {"filled": 0}
    wins = [r for r in filled if r["result"] in ("clean_run", "reclaimed_run")]
    clean = [r for r in filled if r["result"] == "clean_run"]
    reclaimed = [r for r in filled if r["result"] == "reclaimed_run"]
    stopped = [r for r in filled if r["result"] == "stopped"]
    timed_out = [r for r in filled if r["result"] == "timed_out"]
    strong = [r for r in filled if r["m1_confirm_score"] >= 0.7]
    weak = [r for r in filled if r["m1_confirm_score"] < 0.7]
    return {
        "filled": len(filled),
        "wins": len(wins),
        "winRate": round(len(wins) / len(filled) * 100.0, 2),
        "cleanRuns": len(clean),
        "reclaimedRuns": len(reclaimed),
        "stopped": len(stopped),
        "timedOut": len(timed_out),
        "avgPnlR": round(mean(r["pnl_r"] for r in filled), 4),
        "avgMaeR": round(mean(r["mae_r"] for r in filled), 4),
        "avgMfeR": round(mean(r["mfe_r"] for r in filled), 4),
        "avgRiskPct": round(mean(r["risk_pct"] for r in filled), 4),
        "avgTimeToTargetBars": round(mean(r["time_to_target_bars"] for r in wins if r["time_to_target_bars"] is not None), 2) if wins else None,
        "avgTimeToStopBars": round(mean(r["time_to_stop_bars"] for r in stopped if r["time_to_stop_bars"] is not None), 2) if stopped else None,
        "flipAfterStop": sum(int(bool(r["flip_run_after_stop"])) for r in filled),
        "strongM1": {
            "count": len(strong),
            "winRate": round(sum(1 for r in strong if r["result"] in ("clean_run", "reclaimed_run")) / len(strong) * 100.0, 2) if strong else None,
            "avgPnlR": round(mean(r["pnl_r"] for r in strong), 4) if strong else None,
        },
        "weakM1": {
            "count": len(weak),
            "winRate": round(sum(1 for r in weak if r["result"] in ("clean_run", "reclaimed_run")) / len(weak) * 100.0, 2) if weak else None,
            "avgPnlR": round(mean(r["pnl_r"] for r in weak), 4) if weak else None,
        },
    }


def evaluate_match(
    match: dict,
    template_rows_by_ts: dict[int, dict],
    rows_1m: list[dict],
    ts_1m: list[int],
) -> list[dict]:
    direction = match["direction"]
    release_meta = match.get("evidence", {}).get("release", {})
    best_release = release_meta.get("bestRelease")
    best_release_lead = None
    if best_release is not None:
        release_ts = int(best_release["timestamp"])
        best_release_lead = best_release.get("leadVolume")
    else:
        release_ts = parse_local_ts(match["keyTimes"]["release"])
    release_row = template_rows_by_ts.get(release_ts)
    if not release_row:
        return []

    reset_ts = parse_local_ts(match["keyTimes"]["reset"])
    liquidity_ts = parse_local_ts(match["keyTimes"]["liquiditySeeker"])
    reset_row = template_rows_by_ts.get(reset_ts)
    liquidity_row = template_rows_by_ts.get(liquidity_ts)

    start_idx = bisect.bisect_left(ts_1m, release_ts)
    if start_idx >= len(rows_1m):
        return []

    records = []
    signal_high = to_num(release_row["high"])
    signal_low = to_num(release_row["low"])
    signal_close = to_num(release_row["close"])
    signal_range = max(0.5, signal_high - signal_low)
    for entry_model in BASE_ENTRY_MODELS:
        if entry_model == "first_m1_confirm":
            confirm_idx = find_first_m1_confirm_index(rows_1m, start_idx, direction)
            if confirm_idx is None:
                continue
            signal_for_trade = rows_1m[confirm_idx]
            entry_index = confirm_idx
            sig_high = to_num(signal_for_trade["high"])
            sig_low = to_num(signal_for_trade["low"])
            sig_close = to_num(signal_for_trade["close"])
        else:
            # emulate the release-candle entry model via the closest 1m bar at/after release
            entry_index = start_idx
            if entry_model == "close":
                sig_high = signal_high
                sig_low = signal_low
                sig_close = signal_close
            elif entry_model == "retrace_40":
                sig_high = signal_high
                sig_low = signal_low
                sig_close = signal_close - signal_range * 0.40 if direction == "long" else signal_close + signal_range * 0.40
            else:
                sig_high = signal_high
                sig_low = signal_low
                sig_close = signal_close - signal_range * 0.50 if direction == "long" else signal_close + signal_range * 0.50

        for stop_model in STOP_MODELS:
            for target_model in TARGET_MODELS:
                outcome = evaluate_trade_from_entry_row(
                    rows_1m,
                    entry_index=entry_index,
                    direction=direction,
                    signal_high=sig_high,
                    signal_low=sig_low,
                    signal_close=sig_close,
                    stop_model=stop_model,
                    target_model=target_model,
                    reset_row=reset_row,
                    liquidity_row=liquidity_row,
                )
                if not outcome.get("filled"):
                    continue
                outcome.update(
                    {
                        "templateTf": match["templateTf"],
                        "initialTs": match["initialTs"],
                        "initialTime": match["initialTime"],
                        "releaseTs": release_ts,
                        "entryModel": entry_model,
                        "stopModel": stop_model,
                        "targetModel": target_model,
                        "patternScore": match["score"],
                        "m1_confirm_score": float(match["components"]["m1Confirm"]),
                        "compressionScore": float(match["components"]["compression"]),
                        "releaseScore": float(match["components"]["release"]),
                        "leadVolume": best_release_lead,
                    }
                )
                records.append(outcome)
    return records


def evaluate_tf_matches(
    tf: str,
    matches: list[dict],
    rows_1m: list[dict],
    template_rows_by_ts: dict[int, dict],
) -> dict:
    ts_1m = [int(r["timestamp"]) for r in rows_1m]
    all_records = []
    for match in matches:
        all_records.extend(evaluate_match(match, template_rows_by_ts, rows_1m, ts_1m))

    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for record in all_records:
        key = (record["entryModel"], record["stopModel"], record["targetModel"])
        grouped.setdefault(key, []).append(record)

    leaderboard = []
    for (entry_model, stop_model, target_model), records in grouped.items():
        summary = summarize_records(records)
        if summary.get("filled", 0) == 0:
            continue
        leaderboard.append(
            {
                "entryModel": entry_model,
                "stopModel": stop_model,
                "targetModel": target_model,
                **summary,
            }
        )
    leaderboard.sort(key=lambda item: (-float(item["winRate"]), -float(item["avgPnlR"]), -int(item["filled"])))
    return {
        "templateTf": tf,
        "matchCount": len(matches),
        "leaderboard": leaderboard[:12],
        "allSetups": leaderboard,
    }


def render_markdown(report: dict) -> str:
    lines = [f"# Pattern Match Outcome Evaluator · {report['profileLabel']}", ""]
    for tf in report["timeframes"]:
        block = report["results"][tf]
        lines.extend(
            [
                f"## {tf}",
                "",
                f"- Matches: `{block['matchCount']}`",
                "",
            ]
        )
        for item in block["leaderboard"][:5]:
            lines.extend(
                [
                    f"### {item['entryModel']} | {item['stopModel']} | {item['targetModel']}",
                    f"- filled: `{item['filled']}`",
                    f"- winRate: `{item['winRate']}%`",
                    f"- avgPnlR: `{item['avgPnlR']}`",
                    f"- clean / reclaimed / stopped / timedOut: `{item['cleanRuns']} / {item['reclaimedRuns']} / {item['stopped']} / {item['timedOut']}`",
                    f"- strong M1: `{item['strongM1']}`",
                    f"- weak M1: `{item['weakM1']}`",
                    "",
                ]
            )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate reference-pattern matches against forward outcomes.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--min-score", type=float, default=50.0)
    parser.add_argument("--top-n", type=int, default=5000)
    parser.add_argument("--tfs", nargs="+", default=["15m", "30m", "5m"])
    parser.add_argument("--output-prefix", default="pattern_match_outcomes_v1")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    profile = load_json(Path(args.profile))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    scan = scan_all_timeframes(conn, profile, None, None, args.top_n, args.min_score, include_all=True)
    rows_1m = load_timeframe_rows(conn, "1m")

    results = {}
    for tf in args.tfs:
        matches = scan[tf]["long"].get("allMatches", [])
        template_rows = load_timeframe_rows(conn, tf)
        template_rows_by_ts = {int(r["timestamp"]): r for r in template_rows}
        results[tf] = evaluate_tf_matches(tf, matches, rows_1m, template_rows_by_ts)

    report = {
        "profileLabel": profile["label"],
        "profileWindow": profile["window"],
        "timeframes": args.tfs,
        "results": results,
    }

    json_path = OUT_DIR / f"{args.output_prefix}.json"
    md_path = OUT_DIR / f"{args.output_prefix}.md"
    json_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(render_markdown(report))
    print(json_path)
    print(md_path)


if __name__ == "__main__":
    main()
