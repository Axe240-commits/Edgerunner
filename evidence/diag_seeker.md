# Seeker Kill + Retest Backtest

Mode: **diagnose** | TFs: 4h/1h | Costs: 5.0+5.0 bps/side | Train-Fraction: 0.65

Kills (train): 222, entered (variant A): 154, entry quote: 0.6937
Outcomes: `{"entered": 154, "missed/no_retest": 19, "missed/no_exit_close": 49}`
Retest speed (exec bars): `{"n": 154, "mean": 17.9481, "p25": 2.0, "p50": 8.5, "p75": 24.5, "p90": 52.4}`

## variant_a — targets
- 1.5R: `{"n": 154, "wins": 71, "win_rate": 0.461, "wilson95": [0.3842, 0.5398], "net_r": -1.9, "expectancy_r": -0.0124, "avg_win_r": 1.262, "avg_loss_r": 1.103, "breakeven_wr": 0.4663}`
- 2R: `{"n": 154, "wins": 58, "win_rate": 0.3766, "wilson95": [0.304, 0.4553], "net_r": -11.62, "expectancy_r": -0.0755, "avg_win_r": 1.639, "avg_loss_r": 1.111, "breakeven_wr": 0.4041}`
- 3R: `{"n": 154, "wins": 48, "win_rate": 0.3117, "wilson95": [0.2439, 0.3887], "net_r": -17.48, "expectancy_r": -0.1135, "avg_win_r": 2.057, "avg_loss_r": 1.096, "breakeven_wr": 0.3477}`
- opposite_swing: `{"n": 148, "wins": 64, "win_rate": 0.4324, "wilson95": [0.3553, 0.513], "net_r": 6.22, "expectancy_r": 0.042, "avg_win_r": 1.536, "avg_loss_r": 1.096, "breakeven_wr": 0.4165}`

## variant_b — targets
- 1.5R: `{"n": 79, "wins": 43, "win_rate": 0.5443, "wilson95": [0.435, 0.6495], "net_r": 6.98, "expectancy_r": 0.0883, "avg_win_r": 1.162, "avg_loss_r": 1.194, "breakeven_wr": 0.5068}`
- 2R: `{"n": 79, "wins": 40, "win_rate": 0.5063, "wilson95": [0.3984, 0.6137], "net_r": 15.44, "expectancy_r": 0.1954, "avg_win_r": 1.55, "avg_loss_r": 1.194, "breakeven_wr": 0.4351}`
- 3R: `{"n": 79, "wins": 33, "win_rate": 0.4177, "wilson95": [0.3153, 0.5278], "net_r": 13.97, "expectancy_r": 0.1768, "avg_win_r": 2.107, "avg_loss_r": 1.208, "breakeven_wr": 0.3644}`
- opposite_swing: `{"n": 76, "wins": 32, "win_rate": 0.4211, "wilson95": [0.3165, 0.5332], "net_r": 33.73, "expectancy_r": 0.4439, "avg_win_r": 2.714, "avg_loss_r": 1.207, "breakeven_wr": 0.3078}`

## Kill+Div split (variant A, 2R)
`{"divs>0": {"n": 119, "wins": 44, "win_rate": 0.3697, "wilson95": [0.2884, 0.4593], "net_r": -8.42, "expectancy_r": -0.0708, "avg_win_r": 1.655, "avg_loss_r": 1.083, "breakeven_wr": 0.3956}, "divs=0": {"n": 35, "wins": 14, "win_rate": 0.4, "wilson95": [0.2555, 0.5643], "net_r": -3.2, "expectancy_r": -0.0915, "avg_win_r": 1.588, "avg_loss_r": 1.211, "breakeven_wr": 0.4327}}`

## Loss anatomy (baseline)
`{"all": {"n": 96, "avg_r_net": -1.1113, "avg_cost_r": 0.1628}, "stop": {"n": 89, "avg_r_net": -1.1695, "avg_cost_r": 0.1695}, "timeout": {"n": 7, "avg_r_net": -0.3712, "avg_cost_r": 0.0775}}`

## By direction (baseline)
- long: `{"n": 77, "wins": 30, "win_rate": 0.3896, "wilson95": [0.2884, 0.5013], "net_r": -5.98, "expectancy_r": -0.0777, "avg_win_r": 1.622, "avg_loss_r": 1.163, "breakeven_wr": 0.4175}`
- short: `{"n": 77, "wins": 28, "win_rate": 0.3636, "wilson95": [0.2651, 0.4752], "net_r": -5.64, "expectancy_r": -0.0732, "avg_win_r": 1.657, "avg_loss_r": 1.062, "breakeven_wr": 0.3906}`

## By half-year (baseline)
- 2025-H1: `{"n": 64, "wins": 24, "win_rate": 0.375, "wilson95": [0.2667, 0.4975], "net_r": -4.03, "expectancy_r": -0.063, "avg_win_r": 1.726, "avg_loss_r": 1.136, "breakeven_wr": 0.397}`
- 2025-H2: `{"n": 85, "wins": 32, "win_rate": 0.3765, "wilson95": [0.2809, 0.4827], "net_r": -7.44, "expectancy_r": -0.0875, "avg_win_r": 1.559, "avg_loss_r": 1.081, "breakeven_wr": 0.4096}`
- 2026-H1: `{"n": 5, "wins": 2, "win_rate": 0.4, "wilson95": [0.1176, 0.7693], "net_r": -0.15, "expectancy_r": -0.0308, "avg_win_r": 1.879, "avg_loss_r": 1.304, "breakeven_wr": 0.4097}`

## Cost sensitivity (baseline)
- 2+5 bps: `{"n": 154, "wins": 58, "win_rate": 0.3766, "wilson95": [0.304, 0.4553], "net_r": -4.01, "expectancy_r": -0.026, "avg_win_r": 1.689, "avg_loss_r": 1.062, "breakeven_wr": 0.3861}`
- 5+5 bps: `{"n": 154, "wins": 58, "win_rate": 0.3766, "wilson95": [0.304, 0.4553], "net_r": -11.62, "expectancy_r": -0.0755, "avg_win_r": 1.639, "avg_loss_r": 1.111, "breakeven_wr": 0.4041}`
- 10+10 bps: `{"n": 154, "wins": 57, "win_rate": 0.3701, "wilson95": [0.2979, 0.4487], "net_r": -37.01, "expectancy_r": -0.2403, "avg_win_r": 1.497, "avg_loss_r": 1.261, "breakeven_wr": 0.4572}`
