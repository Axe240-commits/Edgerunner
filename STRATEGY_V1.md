# STRATEGY_V1 — Breaker Zone (Long/Short)

Status: Entwurf v1, von Albert freigegeben (2026-07-24). Zweck: falsifizierbare
Regeln für den ehrlichen Backtest. Keine Änderung ohne neue Version.

## Grundgesetz

Initial Breakout → Konsolidierung/Korrektur → Entscheidung.

Nach jedem echten Ausbruch kommt der Markt zurück und prüft die Break-Kerze.
Hält die Zone, geht die Bewegung weiter (Breaker-Trade). Hält sie nicht, war
der Ausbruch eine Falle (Flip-Kandidat, v2).

## Setup SHORT (Long ist gespiegelt)

1. **Strukturbruch (H1):** Eine H1-Kerze schließt UNTER einem H1-Swing-Low
   (`bos_bear`, `break_depth` > 0). Diese Kerze ist die Breaker-Kerze.
   Docht-Brüche ohne Schluss darunter zählen nicht.
2. **Zone:** Breaker-Kerze von Open bis High (Bruchlevel liegt darunter).
3. **Rücklauf-Filter:** Kurs steigt zurück in die Zone, aber
   Rücklauf-Volumen und -Delta sind schwächer als der Impuls nach unten
   (`vol_vs_ma` fallend, `delta_pct` schwächer als während des Bruchs).
   Kein Rücklauf über das Breaker-High — sonst ist das Setup tot.
4. **Einstieg:** Eine M15-Kerze schließt bärisch aus der Zone heraus
   (Close unter dem Zonen-Unterrand). Kein blinder Limit-Touch.
5. **Stop:** Breaker-High + 0.25 × ATR(14, H1).
6. **Ziel (Baseline):** 2R. Alternativ-Auswertung: TP1 am Tief nach dem Bruch.
7. **Gültigkeit:** 48 H1-Kerzen (2 Tage) ab Bruch. Danach oder bei H1-Schluss
   über dem Breaker-High: Setup ungültig.

## LONG (gespiegelt)

H1-Schluss ÜBER H1-Swing-High (`bos_bull`); Zone = Breaker-Kerze Low bis Open;
Rücklauf nach unten mit schwächerem Volumen/Delta; Einstieg bei M15-Schluss
bullisch aus der Zone; Stop unter Breaker-Low − 0.25 × ATR; Ziel 2R.

## Diagnose-Ziele des Backtests (Exploration nur auf Train-Daten)

Diese Fragen soll der Backtest beantworten, um v2 zu justieren:

- Wie tief läuft der durchschnittliche Rücklauf in die Zone (in % der
  Impulsstrecke und in % der Zonenhöhe)? → optimaler Einstiegspunkt
- Welches Target ist am robustesten: 1.5R / 2R / 3R / Tief-nach-Bruch?
- Wo sitzt der Stop am besten: Breaker-High + Puffer vs. Rücklauf-Hoch?
- Wie oft kommt der Rücklauf gar nicht (verpasste Trades)?

## Disziplin-Regeln für die Justierung

- Parameter-Exploration NUR auf den ersten 65 % der Daten (Train).
- Danach EINE finale Konfiguration, EIN Out-of-Sample-Lauf (35 %).
- Kill-Kriterium (OOS, nach Kosten 5+5 bps): Netto-Expectancy <= 0 R,
  oder n < 100 Trades, oder das Wilson-95%-CI der Win-Rate schließt den
  Breakeven ein. Dann: eine fundierte Variante, danach ist die Familie tot.
- Erfolg heißt: OOS Netto-Expectancy > 0 mit CI, das 0 ausschließt.

## Kosten-Annahmen

Fee 5 bps + Slippage 5 bps pro Seite (Binance BTCUSDT Perp, konservativ).

## v2 (2026-07-24, von Albert freigegeben)

Diagnose-Befund v1: 75 % der Setups sterben vor dem Einstieg (M15-Bestätigung
zu spät), Kosten fressen 0.65R bei engen Zonen-Stops, Ziel 2R zu klein.
v2 adressiert genau diese drei Todesursachen:

1. **Ziel:** Post-Break-Extrem (letztes Low/High nach dem Bruch), nicht 2R.
2. **Einstieg (M1-Trigger):** Nach Zone-Touch: M1-Strukturbruch in
   Bruchrichtung (Short: M1 bildet Lower High im Rücklauf, `bos_bear` auf M1
   bricht dessen Tief). Einstieg = Schluss der M1-Bruchkerze.
   (Docht/Rejection-Variante zurückgestellt als v2b.)
3. **Stop:** hinter dem M1-Rücklauf-Extrem + 0.1 × ATR(14, M1).
4. **Invalidierung:** M15-Schluss jenseits Breaker-Extrem (statt H1) oder
   48 H1-Kerzen.
5. **Kosten:** 2 bps Fee + 5 bps Slippage pro Seite (Limit-orientiert);
   Sensitivität gegen 5+5 und 10+10 ausweisen.
6. Warnung (gemessen, nicht gehofft): enge M1-Stops erhöhen die
   Kostenlast in R; v2 muss das per großem R-Ziel überkompensieren.
