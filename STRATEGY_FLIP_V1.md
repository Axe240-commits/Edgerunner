# STRATEGY_FLIP_V1 — Fakeout-Flip (Reclaim)

Status: Entwurf v1, Richtung von Albert freigegeben (2026-07-24).
Vorgeschichte: Die Breaker-Familie (STRATEGY_V1.md) ist nach v1 (−0.62R) und
v2 (−0.49R) auf Train beerdigt. Zentraler Befund: 75–83 % der H1-Ausbrüche
halten die Breaker-Zone NICHT. Die Flip-Karte spielt genau diese Seite.

## Grundgesetz (gleiches Gesetz, andere Seite)

Initial Breakout → Konsolidierung/Korrektur → Entscheidung.
Hält die Zone nicht (statistisch der Normalfall), war der Ausbruch eine
Falle: Der Rücklauf läuft durch die Zone hindurch zurück über das
Bruchlevel — der Reclaim. Die gefangenen Breakout-Trader müssen aussteigen,
ihre Orders treiben die Flip-Bewegung.

## Setup LONG auf gescheiterten Bär-Bruch (Short gespiegelt)

1. **Bruch:** H1-Kerze schließt UNTER H1-Swing-Low (`bos_bear`,
   `break_depth` > 0). Bruchlevel = das gebrochene Swing-Low.
2. **Post-Break-Extrem:** tiefstes Low nach dem Bruch (die Falle).
3. **Reclaim (Trigger):** Eine M15-Kerze schließt zurück ÜBER dem
   Bruchlevel. Einstieg = Schluss dieser Kerze.
4. **Stop:** Post-Break-Extrem − 0.1 × ATR(14, M15).
5. **Ziel (Baseline):** 2R. Alternativ-Auswertung: letztes H1-Swing-High
   vor dem Bruch (Range-Rotation), 1.5R, 3R.
6. **Gültigkeit:** Reclaim muss innerhalb von 48 H1-Kerzen nach dem Bruch
   kommen, sonst ist der Ausbruch „echt" und das Setup tot.

## Kosten

5 bps Fee + 5 bps Slippage pro Seite (Baseline); Sensitivität 2+5 und 10+10.

## Diagnose-Fragen (nur Train 65 %)

- Wie oft kommt der Reclaim (Quote der gescheiterten Ausbrüche)?
- Expectancy/Win-Rate je Target (1.5R/2R/3R/Swing-High)?
- Rücklauf-Geschwindigkeit: Wie viele M15-Kerzen vom Bruch bis Reclaim?
- Long- vs. Short-Flips, je Halbjahr.

## Disziplin / Kill-Kriterien

Wie bisher: Tuning nur auf Train, ein OOS-Lauf nur wenn Train > 0.
OOS-Kill: Netto-Expectancy <= 0, n < 100, oder Wilson-95%-CI schließt
Breakeven ein → Familie tot.
