# Edgerunner — BTC Trading Signal Analyzer

## Konzept
History laden → Kerzen analysieren → Signale labeln → Muster finden → PyTorch trainieren

## Datenquellen
- **Binance API**: OHLCV + aggTrades (Delta)
- **Shadow Tracker**: Whale Features (Tiers, Sentiment, Cluster)
- **Execution**: Hyperliquid

## Feature-Set pro Kerze (~86 Features)

### Rohdaten (7)
1. Timestamp
2. Open
3. High
4. Low
5. Close
6. Volume
7. Delta (Buy Vol - Sell Vol)

### Kerzen-Anatomie (8)
8. Body Size
9. Upper Wick Length
10. Lower Wick Length
11. Total Range (high - low)
12. Body Ratio (body / range)
13. Wick Ratio (upper / lower)
14. Body Position ((close - low) / range)
15. Is Bullish (bool)

### Volume/Delta (3)
16. Delta % (delta / volume)
17. Volume vs MA (volume / vol_sma_20)
18. Delta vs MA

### Struktur — Swing High/Low (7)
19. Is Swing High (konfigurierbarer Lookback L/R)
20. Is Swing Low (konfigurierbarer Lookback L/R)
21. BOS Bull (Close ueber letztem Swing High)
22. BOS Bear (Close unter letztem Swing Low)
23. CHoCH (erster BOS gegen den Trend)
24. Distance to Last Swing High (%)
25. Distance to Last Swing Low (%)

### Struktur — Break-Qualitaet (9)
26. BOS mit Body (bool)
27. BOS nur mit Wick (bool)
28. Bruch-Tiefe (close - swing_level) / ATR
29. Alter des gebrochenen Swings (Kerzen)
30. Alter normalisiert
31. Anzahl gleichzeitig gebrochene Highs
32. Anzahl gleichzeitig gebrochene Lows
33. Max Alter der gebrochenen Strukturen
34. Min Alter der gebrochenen Strukturen

### Struktur — Paarung Brecher vs Swing-Kerze (14)
35. Swing: Body Ratio
36. Swing: Wick Ratio
37. Swing: Delta %
38. Swing: Volume rel.
39. Swing: Bullish/Bearish
40. Swing: Body Position
41. Swing: OHLC (Open, High, Low, Close)
42-44. Volume/Delta/Body Ratio (Brecher vs Swing)
45. Gleiche Richtung (bool)

### Struktur — Kette (3)
46. Swing hatte selbst Break (bool)
47. Chain Depth (0, 1, 2...)
48. Prev Swing Features (rekursiv)

### Struktur — Cluster (3)
49. Cluster Preis-Range
50. Cluster Preis-Range / ATR
51. Cluster Spread (Max Alter - Min Alter)

### MACD + Divergenzen — Eddiecator/Dumb Money Formel (8)
MACD Settings: EMA 5 / EMA 13 / Signal 1

52. MACD Line
53. MACD Peak (bool) — 3 fallende Bars, alle ueber 0
54. MACD Trough (bool) — 3 steigende Bars, alle unter 0
55. Bullish Divergenz (Price LL + MACD HL)
56. Bearish Divergenz (Price HH + MACD LH)
57. Divergenz + nahe Daily High/Low (Eddie-Filter)
58. Divergenz-Staerke (normalisiert)
59. Divergenz-Breite (Kerzen)

### Kontext/Trend (6)
60. EMA 21 Distance (ATR-normalisiert)
61. EMA 50 Distance
62. EMA 200 Distance
63. ATR 14
64. RSI 14
65. VWAP Distance

### Multi-Timeframe (4)
66. HTF Trend (bullish/bearish/ranging)
67. HTF nearest Swing High
68. HTF nearest Swing Low
69. HTF BOS aktiv (bool)

### Whale Features — Shadow Tracker (8)
70. Whale Sentiment Score
71. Whale Sentiment Confidence
72. Bull Pressure
73. Bear Pressure
74. Whale Cluster aktiv (bool)
75. Whale Cluster Staerke
76. Whale Cluster Richtung
77. Elite Whale Activity (bool)

## Workflow
1. `load_history` — Binance 1min Kerzen + aggTrades laden
2. `compute_features` — Alle Features berechnen
3. `show <candle_id>` — Einzelne Kerze inspizieren
4. `label <candle_id> long/short` — Signal markieren
5. `compare` — Alle gelabelten Signale vergleichen, Gemeinsamkeiten finden
6. `train` — PyTorch Modell trainieren

## Tech Stack
- Python 3.12
- SQLite (Datenspeicher)
- PyTorch + CUDA (RTX 5090, 32GB VRAM)
- Binance API (Daten)
- Hyperliquid (Execution)

## Divergenz-Formel (aus Eddiecator_MACD_VOB_DHL5.mq4)
```
Peak: MACD[i] > MACD[i-1] > MACD[i-2] > MACD[i-3] > 0
Trough: MACD[i] < MACD[i-1] < MACD[i-2] < MACD[i-3] < 0

GetLastPeak: lokales Maximum mit 2+ Bars fallend in jede Richtung
GetLastTrough: lokales Minimum mit 2+ Bars steigend in jede Richtung

Bullish Div: Price Lower Low + MACD Higher Low (+ nahe Daily Low)
Bearish Div: Price Higher High + MACD Lower High (+ nahe Daily High)
```
