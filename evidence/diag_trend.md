# Continuation Backtest (anti mean-reversion card)

Mode: **diagnose** | TFs: 4h/1h | Costs: 5.0+5.0 bps/side | Train-Fraction: 0.65

Breaks (train): 236, entered: 236
Outcomes: `{"entered": 236}`

## Target comparison
- 2R: `{"n": 236, "wins": 87, "win_rate": 0.3686, "wilson95": [0.3097, 0.4318], "net_r": -19.85, "expectancy_r": -0.0841, "avg_win_r": 1.643, "avg_loss_r": 1.092, "breakeven_wr": 0.3994}`
- 3R: `{"n": 236, "wins": 74, "win_rate": 0.3136, "wilson95": [0.2578, 0.3753], "net_r": -24.37, "expectancy_r": -0.1032, "avg_win_r": 2.041, "avg_loss_r": 1.083, "breakeven_wr": 0.3466}`
- 5R: `{"n": 236, "wins": 69, "win_rate": 0.2924, "wilson95": [0.238, 0.3534], "net_r": -28.91, "expectancy_r": -0.1225, "avg_win_r": 2.196, "avg_loss_r": 1.081, "breakeven_wr": 0.3298}`
- chandelier_3atr: `{"n": 236, "wins": 81, "win_rate": 0.3432, "wilson95": [0.2856, 0.4059], "net_r": -28.51, "expectancy_r": -0.1208, "avg_win_r": 1.544, "avg_loss_r": 0.991, "breakeven_wr": 0.3909}`

## Loss anatomy (baseline 3R)
`{"all": {"n": 162, "avg_r_net": -1.0827, "avg_cost_r": 0.1152}, "stop": {"n": 155, "avg_r_net": -1.1155, "avg_cost_r": 0.1155}, "timeout": {"n": 7, "avg_r_net": -0.3546, "avg_cost_r": 0.1066}}`

## By direction (baseline 3R)
- long: `{"n": 117, "wins": 37, "win_rate": 0.3162, "wilson95": [0.239, 0.4052], "net_r": -5.26, "expectancy_r": -0.045, "avg_win_r": 2.196, "avg_loss_r": 1.081, "breakeven_wr": 0.33}`
- short: `{"n": 119, "wins": 37, "win_rate": 0.3109, "wilson95": [0.2348, 0.3989], "net_r": -19.1, "expectancy_r": -0.1605, "avg_win_r": 1.886, "avg_loss_r": 1.084, "breakeven_wr": 0.365}`

## By half-year (baseline 3R)
- 2025-H1: `{"n": 97, "wins": 31, "win_rate": 0.3196, "wilson95": [0.2352, 0.4177], "net_r": -7.26, "expectancy_r": -0.0749, "avg_win_r": 2.052, "avg_loss_r": 1.074, "breakeven_wr": 0.3435}`
- 2025-H2: `{"n": 130, "wins": 39, "win_rate": 0.3, "wilson95": [0.2279, 0.3836], "net_r": -20.75, "expectancy_r": -0.1596, "avg_win_r": 2.018, "avg_loss_r": 1.093, "breakeven_wr": 0.3513}`
- 2026-H1: `{"n": 9, "wins": 4, "win_rate": 0.4444, "wilson95": [0.1888, 0.7334], "net_r": 3.65, "expectancy_r": 0.4051, "avg_win_r": 2.174, "avg_loss_r": 1.01, "breakeven_wr": 0.3172}`

## Cost sensitivity (baseline 3R)
- 2+5 bps: `{"n": 236, "wins": 75, "win_rate": 0.3178, "wilson95": [0.2617, 0.3797], "net_r": -16.16, "expectancy_r": -0.0685, "avg_win_r": 2.049, "avg_loss_r": 1.055, "breakeven_wr": 0.3399}`
- 5+5 bps: `{"n": 236, "wins": 74, "win_rate": 0.3136, "wilson95": [0.2578, 0.3753], "net_r": -24.37, "expectancy_r": -0.1032, "avg_win_r": 2.041, "avg_loss_r": 1.083, "breakeven_wr": 0.3466}`
- 10+10 bps: `{"n": 236, "wins": 73, "win_rate": 0.3093, "wilson95": [0.2538, 0.371], "net_r": -51.73, "expectancy_r": -0.2192, "avg_win_r": 1.95, "avg_loss_r": 1.191, "breakeven_wr": 0.3791}`
