#!/usr/bin/env python3
"""
Targeted candle feature recompute for an exact time range.

Use this when a stored feature window is known to be wrong/defaulted and you
want to overwrite only that slice with freshly analyzed candles plus enough
left/right context for the analyzer.
"""

import argparse
from datetime import datetime

from candle_analyzer import CandleAnalyzer
from db import DEFAULT_DB_PATH, insert_candles
from hyperliquid_api import (
    INTERVAL_MS,
    fetch_binance_futures_volume,
    fetch_binance_volume,
    fetch_candles,
    merge_binance_volume,
)
from history_loader import TF_LOOKBACK


def _parse_ts(value: str) -> int:
    value = value.strip()
    if value.isdigit():
        return int(value)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(value, fmt).timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"Unsupported timestamp format: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute candle features for a targeted range")
    parser.add_argument("--tf", default="1m", help="Timeframe, e.g. 1m, 5m, 15m")
    parser.add_argument("--start", required=True, help="Insert window start (ms or YYYY-MM-DD[ HH:MM[:SS]])")
    parser.add_argument("--end", required=True, help="Insert window end (ms or YYYY-MM-DD[ HH:MM[:SS]])")
    parser.add_argument("--context-before-bars", type=int, default=120, help="Extra bars before start for analyzer context")
    parser.add_argument("--context-after-bars", type=int, default=40, help="Extra bars after end for analyzer context")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--coin", default="BTC", help="Hyperliquid coin")
    args = parser.parse_args()

    ims = INTERVAL_MS.get(args.tf)
    if not ims:
        raise SystemExit(f"Unknown timeframe: {args.tf}")

    start_ms = _parse_ts(args.start)
    end_ms = _parse_ts(args.end)
    if end_ms < start_ms:
        raise SystemExit("--end must be >= --start")

    context_start = start_ms - args.context_before_bars * ims
    context_end = end_ms + args.context_after_bars * ims

    raw = fetch_candles(args.coin, args.tf, start_ms=context_start, end_ms=context_end, limit=5000)
    if not raw:
        raise SystemExit("No candles returned for requested range")

    bv = fetch_binance_volume(
        args.tf,
        start_ms=raw[0]["timestamp"],
        end_ms=raw[-1]["timestamp"] + ims,
        limit=max(1000, len(raw) + 10),
    )
    try:
        fv = fetch_binance_futures_volume(
            args.tf,
            start_ms=raw[0]["timestamp"],
            end_ms=raw[-1]["timestamp"] + ims,
            limit=max(1000, len(raw) + 10),
        )
    except Exception:
        fv = {}

    merge_binance_volume(raw, bv, fv)

    analyzer = CandleAnalyzer(swing_lookback=TF_LOOKBACK.get(args.tf, 2))
    features = analyzer.analyze_batch(raw)
    raw_by_ts = {c["timestamp"]: c for c in raw}
    for feature in features:
        src = raw_by_ts.get(feature["timestamp"])
        if not src:
            continue
        for key in (
            "spot_volume",
            "spot_delta",
            "futures_volume",
            "futures_delta",
            "futures_minus_spot_volume",
            "futures_minus_spot_delta",
        ):
            if key in src:
                feature[key] = src.get(key)

    subset = [feature for feature in features if start_ms <= feature["timestamp"] <= end_ms]
    insert_candles(subset, tf=args.tf, path=args.db)

    print(
        f"recomputed {len(subset)} candles for {args.tf} "
        f"from {start_ms} to {end_ms} using {len(raw)} context candles"
    )


if __name__ == "__main__":
    main()
