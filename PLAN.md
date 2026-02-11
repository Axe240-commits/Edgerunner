# Edgerunner — BTC Trading Signal Analyzer

## Philosophie
Es gibt nur Kerzen mit Eigenschaften. Seeker, Structure Breaks, Divergenzen —
das sind alles menschliche Labels fuer Muster die sich aus den Rohdaten ergeben.
Wir loggen ALLES pro Kerze ohne Filter und Annahmen. PyTorch findet die echten
Muster — auch solche die kein Mensch gleichzeitig tracken kann.

## Konzept
History laden → Kerzen loggen → Signale labeln → Muster vergleichen → PyTorch trainieren

## Datenquellen
- **Binance API**: OHLCV + aggTrades (Delta) — beste Liquiditaet/History
- **Shadow Tracker**: Whale Features (Tiers, Sentiment, Cluster)
- **Execution**: Hyperliquid (DEX)

## Rohdaten pro Kerze (7 Felder → alles andere berechnet)
1. Timestamp
2. Open
3. High
4. Low
5. Close
6. Volume
7. Delta (Buy Vol - Sell Vol)

## Berechnete Features pro Kerze

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

### Struktur — Paarung Brecher vs Swing-Kerze (13)
35. Swing: Body Ratio
36. Swing: Wick Ratio
37. Swing: Delta %
38. Swing: Volume rel.
39. Swing: Bullish/Bearish
40. Swing: Body Position
41. Swing: OHLC (Open, High, Low, Close)
42. Volume Ratio (Brecher / Swing)
43. Delta Ratio (Brecher / Swing)
44. Body Size Ratio (Brecher / Swing)
45. Gleiche Richtung (bool)
46. Gebrochenes Swing war Seeker (bool) — welche Art?
47. Gebrochenes Swing war Seeker Div Kerze (bool) — welche Div Nr?

### Struktur — Kette (3)
48. Swing hatte selbst Break (bool)
49. Chain Depth (0, 1, 2...)
50. Prev Swing Features (rekursiv)

### Struktur — Cluster (3)
51. Cluster Preis-Range (hoechster - niedrigster gebrochener Swing)
52. Cluster Preis-Range / ATR (normalisiert)
53. Cluster Spread (Max Alter - Min Alter)

### MACD + Divergenzen — Eddiecator/Dumb Money Formel (8)
MACD Settings: EMA 5 / EMA 13 / Signal 1

54. MACD Line
55. MACD Peak (bool) — 3 fallende Bars, alle ueber 0
56. MACD Trough (bool) — 3 steigende Bars, alle unter 0
57. Bullish Divergenz (Price LL + MACD HL)
58. Bearish Divergenz (Price HH + MACD LH)
59. Divergenz + nahe Daily High/Low (Eddie-Filter)
60. Divergenz-Staerke (normalisiert)
61. Divergenz-Breite (Kerzen)

### Seeker — Cycle & Divergenz (10)
Aus seeker_theory.py (Shadow Tracker). Seeker = Swing mit signifikantem Wick.
Seeker Kill = Structure Break. Alles ist eins.

62. Is Seeker HS (bool) — diese Kerze startet einen High Seeker
63. Is Seeker LS (bool) — diese Kerze startet einen Low Seeker
64. Is Seeker Divergenz (bool) — diese Kerze erzeugt eine Div in einem Seeker
65. Seeker Div Nummer (1., 2., 3. Div in diesem Seeker)
66. Abstand zur vorherigen Seeker Div (Kerzen)
67. Abstand zur vorherigen Seeker Div (normalisiert)
68. Is Seeker Kill (bool) — diese Kerze killt einen Seeker (= Structure Break)
69. Killed Seeker Div Count (Divs die der gekillete Seeker hatte)
70. Kerze selbst war Seeker (bool) — welche Art?
71. Kerze selbst war Seeker Div Kerze (bool) — welche Div Nr?

### Kontext/Trend (6)
72. EMA 21 Distance (ATR-normalisiert)
73. EMA 50 Distance
74. EMA 200 Distance
75. ATR 14
76. RSI 14
77. VWAP Distance

### Multi-Timeframe (4)
78. HTF Trend (bullish/bearish/ranging)
79. HTF nearest Swing High
80. HTF nearest Swing Low
81. HTF BOS aktiv (bool)

### Whale Features — Shadow Tracker (8)
82. Whale Sentiment Score
83. Whale Sentiment Confidence
84. Bull Pressure
85. Bear Pressure
86. Whale Cluster aktiv (bool)
87. Whale Cluster Staerke
88. Whale Cluster Richtung
89. Elite Whale Activity (bool)

## Total: 89 Features aus 7 Rohdaten

## Kernprinzip
Jede Kerze wird komplett geloggt. Daraus ergibt sich alles:
- Structure Breaks = Swings die gebrochen werden
- Seeker Kills = Structure Breaks (gleiche Sache, andere Perspektive)
- Paarungen = Brecher-Kerze vs gebrochene Swing-Kerze (mit allen Properties)
- Ketten = rekursiv: hat die Swing-Kerze selbst gebrochen?
- Cluster = mehrere Breaks gleichzeitig mit Preis-Range und Alter
- Seeker Div Sequenzen = Abstand zwischen Divs zeigt Coil-Energie

Durch das Loggen jeder Kerze wissen wir zu jedem Zeitpunkt welche Kerze
welche Beschaffenheit hatte. Ueber die Zeit zeigt sich welche Eigenschaften
eine Seeker-Kerze haben MUSS — nicht manuell definiert, sondern aus Daten gelernt.

## Workflow
1. `load_history` — Binance 1min Kerzen + aggTrades laden
2. `compute_features` — Alle 89 Features berechnen
3. `show <candle_id>` — Einzelne Kerze mit allen Properties inspizieren
4. `label <candle_id> long/short` — Signal markieren
5. `compare` — Alle gelabelten Signale vergleichen, Gemeinsamkeiten finden
6. `train` — PyTorch Modell trainieren (RTX 5090)

## Tech Stack
- Python 3.12
- SQLite (Datenspeicher)
- PyTorch + CUDA (RTX 5090, 32GB VRAM)
- Binance API (Daten)
- Hyperliquid (Execution)

## Divergenz-Formel (aus Eddiecator_MACD_VOB_DHL5.mq4 / Dumb Money.mq4)
```
Peak: MACD[i] > MACD[i-1] > MACD[i-2] > MACD[i-3] > 0
Trough: MACD[i] < MACD[i-1] < MACD[i-2] < MACD[i-3] < 0

GetLastPeak: lokales Maximum mit 2+ Bars fallend in jede Richtung
GetLastTrough: lokales Minimum mit 2+ Bars steigend in jede Richtung

Bullish Div: Price Lower Low + MACD Higher Low (+ nahe Daily Low fuer Eddie-Filter)
Bearish Div: Price Higher High + MACD Lower High (+ nahe Daily High fuer Eddie-Filter)
```

## Seeker Divergenz-Formel (aus seeker_theory.py)
```
Seeker = Swing High/Low mit Wick > 20% der Range
Wick Zone: HS = BodyHigh bis High, LS = Low bis BodyLow

Seeker Div (HS): Kerze beruehrt Zone + body_high > seeker.body_high
Seeker Div (LS): Kerze beruehrt Zone + body_low < seeker.body_low
Kill (HS): Close > High
Kill (LS): Close < Low
```
