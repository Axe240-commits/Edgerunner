# Breaker Backtest — STRATEGY_V1

Mode: **diagnose** | Strategy: **v1** | TFs: 4h/1h | Costs: 5.0+5.0 bps/side | Train-Fraction: 0.65

H1 candles: 3300 (train: 2145)
Setups (train): 236, entered: 43
Outcomes: `{"entered": 43, "invalid": 114, "missed/no_pullback": 55, "skipped": 3, "missed/no_exit_close": 20, "missed/filter_never_passed": 1}`

## Pullback depth
- pullback_depth_pct_impulse: `{"n": 43, "mean": 1.8721, "p25": 1.3198, "p50": 1.5559, "p75": 2.1376, "p90": 2.6322}`
- pullback_depth_pct_zone: `{"n": 43, "mean": 110.9014, "p25": 0.5961, "p50": 1.5005, "p75": 3.9717, "p90": 13.0545}`

## Target comparison (baseline stop)
- 1.5R: `{"n": 43, "wins": 15, "win_rate": 0.3488, "wilson95": [0.2242, 0.4983], "net_r": -17.31, "expectancy_r": -0.4027, "avg_win_r": 1.213, "avg_loss_r": 1.268, "breakeven_wr": 0.5111}`
- 2R: `{"n": 43, "wins": 12, "win_rate": 0.2791, "wilson95": [0.1675, 0.4269], "net_r": -20.25, "expectancy_r": -0.471, "avg_win_r": 1.577, "avg_loss_r": 1.264, "breakeven_wr": 0.4449}`
- 3R: `{"n": 43, "wins": 8, "win_rate": 0.186, "wilson95": [0.0974, 0.3262], "net_r": -26.9, "expectancy_r": -0.6256, "avg_win_r": 2.157, "avg_loss_r": 1.262, "breakeven_wr": 0.369}`
- post_break_extreme: `{"n": 43, "wins": 14, "win_rate": 0.3256, "wilson95": [0.2049, 0.4748], "net_r": -24.29, "expectancy_r": -0.565, "avg_win_r": 0.893, "avg_loss_r": 1.269, "breakeven_wr": 0.5869}`

## Stop variants (baseline target)
- breaker: `{"n": 43, "wins": 12, "win_rate": 0.2791, "wilson95": [0.1675, 0.4269], "net_r": -20.25, "expectancy_r": -0.471, "avg_win_r": 1.577, "avg_loss_r": 1.264, "breakeven_wr": 0.4449}`
- pullback: `{"n": 43, "wins": 12, "win_rate": 0.2791, "wilson95": [0.1675, 0.4269], "net_r": -18.52, "expectancy_r": -0.4306, "avg_win_r": 1.611, "avg_loss_r": 1.221, "breakeven_wr": 0.4311}`

## Missed trades
`{"by_reason": {"no_pullback": 55, "no_exit_close": 20, "filter_never_passed": 1}, "invalid_before_pullback": 0, "invalid_during_pullback": 114, "missed_total": 190, "tracked_total": 233, "missed_quote": 0.8155}`

## Loss anatomy (baseline)
`{"all": {"n": 31, "avg_r_net": -1.2637, "avg_cost_r": 0.2637}, "stop": {"n": 31, "avg_r_net": -1.2637, "avg_cost_r": 0.2637}, "timeout": {"n": 0}}`

## Win-R distribution (baseline)
`{"n": 12, "mean": 1.5767, "p25": 1.5398, "p50": 1.7212, "p75": 1.7844, "p90": 1.8337}`

## By direction (baseline)
- long: `{"n": 16, "wins": 8, "win_rate": 0.5, "wilson95": [0.28, 0.72], "net_r": 3.06, "expectancy_r": 0.191, "avg_win_r": 1.654, "avg_loss_r": 1.272, "breakeven_wr": 0.4347}`
- short: `{"n": 27, "wins": 4, "win_rate": 0.1481, "wilson95": [0.0592, 0.3248], "net_r": -23.31, "expectancy_r": -0.8633, "avg_win_r": 1.423, "avg_loss_r": 1.261, "breakeven_wr": 0.4698}`

## By half-year (baseline)
- 2025-H1: `{"n": 19, "wins": 5, "win_rate": 0.2632, "wilson95": [0.1181, 0.4879], "net_r": -10.57, "expectancy_r": -0.5564, "avg_win_r": 1.348, "avg_loss_r": 1.237, "breakeven_wr": 0.4784}`
- 2025-H2: `{"n": 23, "wins": 6, "win_rate": 0.2609, "wilson95": [0.1255, 0.4647], "net_r": -11.43, "expectancy_r": -0.4968, "avg_win_r": 1.739, "avg_loss_r": 1.286, "breakeven_wr": 0.4251}`
- 2026-H1: `{"n": 1, "wins": 1, "win_rate": 1.0, "wilson95": [0.2065, 1.0], "net_r": 1.75, "expectancy_r": 1.7459, "avg_win_r": 1.746, "avg_loss_r": 0.0, "breakeven_wr": null}`

## Cost sensitivity (baseline config)
- 2+5 bps: `{"n": 43, "wins": 12, "win_rate": 0.2791, "wilson95": [0.1675, 0.4269], "net_r": -16.71, "expectancy_r": -0.3885, "avg_win_r": 1.668, "avg_loss_r": 1.185, "breakeven_wr": 0.4153}`
- 5+5 bps: `{"n": 43, "wins": 12, "win_rate": 0.2791, "wilson95": [0.1675, 0.4269], "net_r": -20.25, "expectancy_r": -0.471, "avg_win_r": 1.577, "avg_loss_r": 1.264, "breakeven_wr": 0.4449}`
- 10+10 bps: `{"n": 43, "wins": 12, "win_rate": 0.2791, "wilson95": [0.1675, 0.4269], "net_r": -32.07, "expectancy_r": -0.7459, "avg_win_r": 1.273, "avg_loss_r": 1.527, "breakeven_wr": 0.5454}`
