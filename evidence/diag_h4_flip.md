# Fakeout-Flip Backtest — STRATEGY_FLIP_V1

Mode: **diagnose** | TFs: 4h/1h | Costs: 5.0+5.0 bps/side | Train-Fraction: 0.65

H1 candles: 3300 (train: 2145)
Setups (train): 236, entered: 191
**Reclaim quote: 0.8093** (of 236 tracked breaks)
Outcomes: `{"entered": 191, "missed/no_reclaim": 45}`

## Reclaim speed (M15 bars)
`{"n": 191, "mean": 13.7382, "p25": 2.0, "p50": 5.0, "p75": 16.5, "p90": 39.0}`

## Target comparison
- 1.5R: `{"n": 191, "wins": 91, "win_rate": 0.4764, "wilson95": [0.4068, 0.547], "net_r": -10.08, "expectancy_r": -0.0528, "avg_win_r": 1.092, "avg_loss_r": 1.094, "breakeven_wr": 0.5006}`
- 2R: `{"n": 191, "wins": 80, "win_rate": 0.4188, "wilson95": [0.3512, 0.4897], "net_r": -10.35, "expectancy_r": -0.0542, "avg_win_r": 1.391, "avg_loss_r": 1.096, "breakeven_wr": 0.4406}`
- 3R: `{"n": 191, "wins": 66, "win_rate": 0.3455, "wilson95": [0.2818, 0.4154], "net_r": -26.87, "expectancy_r": -0.1407, "avg_win_r": 1.693, "avg_loss_r": 1.109, "breakeven_wr": 0.3957}`
- swing_extreme: `{"n": 191, "wins": 80, "win_rate": 0.4188, "wilson95": [0.3512, 0.4897], "net_r": -5.77, "expectancy_r": -0.0302, "avg_win_r": 1.535, "avg_loss_r": 1.158, "breakeven_wr": 0.4301}`

## Loss anatomy (baseline 2R)
`{"all": {"n": 111, "avg_r_net": -1.0961, "avg_cost_r": 0.2035}, "stop": {"n": 96, "avg_r_net": -1.2286, "avg_cost_r": 0.2286}, "timeout": {"n": 15, "avg_r_net": -0.2482, "avg_cost_r": 0.0426}}`

## By direction (baseline 2R)
- long: `{"n": 99, "wins": 42, "win_rate": 0.4242, "wilson95": [0.3315, 0.5226], "net_r": -6.11, "expectancy_r": -0.0617, "avg_win_r": 1.286, "avg_loss_r": 1.055, "breakeven_wr": 0.4506}`
- short: `{"n": 92, "wins": 38, "win_rate": 0.413, "wilson95": [0.3179, 0.5152], "net_r": -4.24, "expectancy_r": -0.0461, "avg_win_r": 1.508, "avg_loss_r": 1.139, "breakeven_wr": 0.4305}`

## By half-year (baseline 2R)
- 2025-H1: `{"n": 75, "wins": 31, "win_rate": 0.4133, "wilson95": [0.3088, 0.5263], "net_r": -2.79, "expectancy_r": -0.0373, "avg_win_r": 1.38, "avg_loss_r": 1.036, "breakeven_wr": 0.4288}`
- 2025-H2: `{"n": 109, "wins": 46, "win_rate": 0.422, "wilson95": [0.3335, 0.5158], "net_r": -7.34, "expectancy_r": -0.0673, "avg_win_r": 1.406, "avg_loss_r": 1.143, "breakeven_wr": 0.4484}`
- 2026-H1: `{"n": 7, "wins": 3, "win_rate": 0.4286, "wilson95": [0.1582, 0.7495], "net_r": -0.22, "expectancy_r": -0.0314, "avg_win_r": 1.291, "avg_loss_r": 1.023, "breakeven_wr": 0.4422}`

## Selection filter (with/without, per target)
`{"mode": "none", "selection_quote": null, "n_all": 191, "n_selected": 191, "active": false, "without_filter": {"1.5R": {"n": 191, "wins": 91, "win_rate": 0.4764, "wilson95": [0.4068, 0.547], "net_r": -10.08, "expectancy_r": -0.0528, "avg_win_r": 1.092, "avg_loss_r": 1.094, "breakeven_wr": 0.5006}, "2R": {"n": 191, "wins": 80, "win_rate": 0.4188, "wilson95": [0.3512, 0.4897], "net_r": -10.35, "expectancy_r": -0.0542, "avg_win_r": 1.391, "avg_loss_r": 1.096, "breakeven_wr": 0.4406}, "3R": {"n": 191, "wins": 66, "win_rate": 0.3455, "wilson95": [0.2818, 0.4154], "net_r": -26.87, "expectancy_r": -0.1407, "avg_win_r": 1.693, "avg_loss_r": 1.109, "breakeven_wr": 0.3957}, "swing_extreme": {"n": 191, "wins": 80, "win_rate": 0.4188, "wilson95": [0.3512, 0.4897], "net_r": -5.77, "expectancy_r": -0.0302, "avg_win_r": 1.535, "avg_loss_r": 1.158, "breakeven_wr": 0.4301}}, "with_filter": {"1.5R": {"n": 191, "wins": 91, "win_rate": 0.4764, "wilson95": [0.4068, 0.547], "net_r": -10.08, "expectancy_r": -0.0528, "avg_win_r": 1.092, "avg_loss_r": 1.094, "breakeven_wr": 0.5006}, "2R": {"n": 191, "wins": 80, "win_rate": 0.4188, "wilson95": [0.3512, 0.4897], "net_r": -10.35, "expectancy_r": -0.0542, "avg_win_r": 1.391, "avg_loss_r": 1.096, "breakeven_wr": 0.4406}, "3R": {"n": 191, "wins": 66, "win_rate": 0.3455, "wilson95": [0.2818, 0.4154], "net_r": -26.87, "expectancy_r": -0.1407, "avg_win_r": 1.693, "avg_loss_r": 1.109, "breakeven_wr": 0.3957}, "swing_extreme": {"n": 191, "wins": 80, "win_rate": 0.4188, "wilson95": [0.3512, 0.4897], "net_r": -5.77, "expectancy_r": -0.0302, "avg_win_r": 1.535, "avg_loss_r": 1.158, "breakeven_wr": 0.4301}}}`

## Cost sensitivity (baseline 2R)
- 2+5 bps: `{"n": 191, "wins": 80, "win_rate": 0.4188, "wilson95": [0.3512, 0.4897], "net_r": 0.61, "expectancy_r": 0.0032, "avg_win_r": 1.444, "avg_loss_r": 1.035, "breakeven_wr": 0.4176}`
- 5+5 bps: `{"n": 191, "wins": 80, "win_rate": 0.4188, "wilson95": [0.3512, 0.4897], "net_r": -10.35, "expectancy_r": -0.0542, "avg_win_r": 1.391, "avg_loss_r": 1.096, "breakeven_wr": 0.4406}`
- 10+10 bps: `{"n": 191, "wins": 75, "win_rate": 0.3927, "wilson95": [0.3262, 0.4634], "net_r": -46.87, "expectancy_r": -0.2454, "avg_win_r": 1.302, "avg_loss_r": 1.246, "breakeven_wr": 0.489}`
