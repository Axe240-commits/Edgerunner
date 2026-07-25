# RESEARCH_FINDINGS — Stand 2026-07-25 (Freestyle-Serie)

8 Strategie-Karten getestet, 1 übersprungen (Datenlage). Alle Messungen:
Train 65 % von 18 Monaten BTC (Binance Futures, echtes Delta), Kosten 5+5 bps
falls nicht anders angegeben, Wilson-95%-CI, point-in-time-korrekt.
Diagnose-Reports: `.runtime/diag_*.json` bzw. `C:\edgerunner\diag_*.md`.

## Scoreboard

| # | Karte | Bestes Train-Ergebnis | Urteil |
|---|---|---|---|
| 1 | H1-Breaker Retest (v1 M15-Conf / v2 M1-Trigger) | −0.62R / −0.49R | tot |
| 2 | H1-Fakeout-Flip (Reclaim) | −0.51R | tot |
| 3 | H4-Breaker Retest | −0.47R (n=43) | tot |
| 4 | H4-Flip | −0.05R (brutto 0R) | tot |
| 5 | Seeker-Kill Retest / Direkt-Entry | −0.08R / −1.07R | tot |
| 6 | H4-Flip + Delta-Selektion | −0.02R | tot (Filter tautologisch, 91 %) |
| 7 | H4-Continuation (BOS + ATR-Stop/Trail) | −0.08R | tot |
| 8 | H4-Flip + Funding-Selektion | **+0.04R** (n=94, CI inkl. Breakeven) | formal tot, einziger positiver Hinweis |
| 9 | OI-konditionierter Flip | — | übersprungen: OI-History < 30 Tage |

## Die drei Gesetzmäßigkeiten

1. **Kosten-R-Skalierung:** Kosten in R ∝ entry/risk. 10 bps fressen 0.65R
   bei 0.3 %-Stops (H1), 0.8R bei M1-Stops, aber nur 0.16R bei H4-Stops
   (2–4 %). Enge Stops = Wand, bevor irgendein Edge zählt.
2. **0R-Konvergenz:** Sobald die Kostenlast unter ~0.2R fällt, landen ALLE
   preisbasierten Varianten — Fade UND Follow, BOS UND Seeker-Kill — bei
   0R ± 0.1R brutto. BTC ist nach H4-Strukturbrüchen beidseitig effizient.
3. **Fallenquote 80–88 %:** Die Mehrheit der H-Strukturbrüche wird reclaimt
   (H1 88.4 %, H4 80.9 %, Median 5 Bars). Robust über Halbjahre. Real —
   aber ohne Zusatzinformation eingepreist und nicht monetisierbar.

## Konsequenzen

- Struktur-Setup-Suche (Preis-only) eingestellt: Kategorie erschöpft,
  beidseitig, über drei TF-Ebenen belegt.
- Einziger positiver Ausschlag der Serie: **nicht-preisbasierte Selektion**
  (Funding/Crowding, +0.1R Uplift durch den Filter). Richtung stimmt,
  n zu dünn für mehr.
- Der Whale-Collector (seit 2026-07-24 live) sammelt genau diese
  Zusatzinformation. In 2–3 Monaten: Funding + Whale als zwei
  Selektionsebenen auf der H4-Flip-Karte nachmessen.
- Bis dahin bleibt Echtgeld tabu. Paper-Pipeline (Funding-Join, Selektion,
  Order-Sim) darf gebaut werden — nicht weil +0.04R tradbar ist, sondern
  um live n zu sammeln.

## Maschinerie (das eigentliche Asset)

`backtest_{breaker,flip,seeker,trend}.py` — TF-generisch, PIT-sicher,
Kosten-ehrlich, Wilson-Disziplin, 28 Unit-Tests. Falsifiziert jede neue
Karte in < 1 min pro Messung. `funding_loader.py` + `funding.db`
(separat, 1712 Prints seit 2025-01).
