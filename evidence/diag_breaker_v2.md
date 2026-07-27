# Breaker Backtest — STRATEGY_V1

Mode: **diagnose** | Strategy: **v2** | TFs: 1h/15m | Costs: 5.0+5.0 bps/side | Train-Fraction: 0.65

H1 candles: 13200 (train: 8580)
Setups (train): 962, entered: 149
Outcomes: `{"invalid": 360, "missed/entry_too_far": 252, "entered": 149, "missed/no_pullback": 141, "skipped": 57, "missed/no_m1_trigger": 3}`

## Pullback depth
- pullback_depth_pct_impulse: `{"n": 149, "mean": 2.3791, "p25": 1.3435, "p50": 1.6884, "p75": 2.3646, "p90": 3.3011}`
- pullback_depth_pct_zone: `{"n": 149, "mean": 0.6976, "p25": 0.2118, "p50": 0.5666, "p75": 1.0126, "p90": 1.5232}`

## Target comparison (baseline stop)
- 1.5R: `{"n": 149, "wins": 40, "win_rate": 0.2685, "wilson95": [0.2038, 0.3448], "net_r": -191.49, "expectancy_r": -1.2852, "avg_win_r": 0.687, "avg_loss_r": 2.009, "breakeven_wr": 0.7451}`
- 2R: `{"n": 149, "wins": 39, "win_rate": 0.2617, "wilson95": [0.1978, 0.3377], "net_r": -192.63, "expectancy_r": -1.2928, "avg_win_r": 1.022, "avg_loss_r": 2.113, "breakeven_wr": 0.6741}`
- 3R: `{"n": 149, "wins": 40, "win_rate": 0.2685, "wilson95": [0.2038, 0.3448], "net_r": -165.62, "expectancy_r": -1.1115, "avg_win_r": 1.841, "avg_loss_r": 2.195, "breakeven_wr": 0.5439}`
- post_break_extreme: `{"n": 146, "wins": 34, "win_rate": 0.2329, "wilson95": [0.1717, 0.3077], "net_r": -137.9, "expectancy_r": -0.9445, "avg_win_r": 2.691, "avg_loss_r": 2.048, "breakeven_wr": 0.4322}`

## Stop variants (baseline target)
- breaker: `{"n": 146, "wins": 47, "win_rate": 0.3219, "wilson95": [0.2515, 0.4014], "net_r": -64.64, "expectancy_r": -0.4427, "avg_win_r": 1.222, "avg_loss_r": 1.233, "breakeven_wr": 0.5023}`
- pullback: `{"n": 146, "wins": 34, "win_rate": 0.2329, "wilson95": [0.1717, 0.3077], "net_r": -137.9, "expectancy_r": -0.9445, "avg_win_r": 2.691, "avg_loss_r": 2.048, "breakeven_wr": 0.4322}`

## Missed trades
`{"by_reason": {"entry_too_far": 252, "no_pullback": 141, "no_m1_trigger": 3}, "invalid_before_pullback": 0, "invalid_during_pullback": 360, "missed_total": 756, "tracked_total": 905, "missed_quote": 0.8354}`

## Loss anatomy (baseline)
`{"all": {"n": 112, "avg_r_net": -2.0482, "avg_cost_r": 1.2835}, "stop": {"n": 91, "avg_r_net": -2.2008, "avg_cost_r": 1.2008}, "timeout": {"n": 7, "avg_r_net": -1.4264, "avg_cost_r": 0.7886}}`

## Win-R distribution (baseline)
`{"n": 34, "mean": 2.6911, "p25": 0.365, "p50": 2.4592, "p75": 4.0394, "p90": 5.7946}`

## By direction (baseline)
- long: `{"n": 69, "wins": 18, "win_rate": 0.2609, "wilson95": [0.1719, 0.3751], "net_r": -68.08, "expectancy_r": -0.9867, "avg_win_r": 2.369, "avg_loss_r": 2.171, "breakeven_wr": 0.4782}`
- short: `{"n": 77, "wins": 16, "win_rate": 0.2078, "wilson95": [0.1321, 0.3112], "net_r": -69.82, "expectancy_r": -0.9067, "avg_win_r": 3.053, "avg_loss_r": 1.945, "breakeven_wr": 0.3892}`

## By half-year (baseline)
- 2025-H1: `{"n": 62, "wins": 17, "win_rate": 0.2742, "wilson95": [0.1788, 0.3959], "net_r": -40.16, "expectancy_r": -0.6477, "avg_win_r": 2.582, "avg_loss_r": 1.868, "breakeven_wr": 0.4197}`
- 2025-H2: `{"n": 77, "wins": 15, "win_rate": 0.1948, "wilson95": [0.1218, 0.2969], "net_r": -77.52, "expectancy_r": -1.0067, "avg_win_r": 2.818, "avg_loss_r": 1.932, "breakeven_wr": 0.4068}`
- 2026-H1: `{"n": 7, "wins": 2, "win_rate": 0.2857, "wilson95": [0.0822, 0.6411], "net_r": -20.23, "expectancy_r": -2.8893, "avg_win_r": 2.665, "avg_loss_r": 5.111, "breakeven_wr": 0.6573}`

## Cost sensitivity (baseline config)
- 2+5 bps: `{"n": 146, "wins": 37, "win_rate": 0.2534, "wilson95": [0.1898, 0.3297], "net_r": -82.63, "expectancy_r": -0.5659, "avg_win_r": 2.816, "avg_loss_r": 1.714, "breakeven_wr": 0.3784}`
- 5+5 bps: `{"n": 146, "wins": 34, "win_rate": 0.2329, "wilson95": [0.1717, 0.3077], "net_r": -137.9, "expectancy_r": -0.9445, "avg_win_r": 2.691, "avg_loss_r": 2.048, "breakeven_wr": 0.4322}`
- 10+10 bps: `{"n": 146, "wins": 20, "win_rate": 0.137, "wilson95": [0.0905, 0.2021], "net_r": -322.13, "expectancy_r": -2.2064, "avg_win_r": 3.107, "avg_loss_r": 3.05, "breakeven_wr": 0.4954}`
