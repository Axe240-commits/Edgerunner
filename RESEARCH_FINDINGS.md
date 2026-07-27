# RESEARCH_FINDINGS — Stand 2026-07-25 (Freestyle-Serie)

8 Strategie-Karten getestet, 1 übersprungen (Datenlage). Alle Messungen:
Train 65 % von 18 Monaten BTC (Binance Futures, echtes Delta), Kosten 5+5 bps
falls nicht anders angegeben, Wilson-95%-CI, point-in-time-korrekt.
Diagnose-Reports: `.runtime/diag_*.json` bzw. `C:\edgerunner\diag_*.md`.

## Scoreboard (alt = v1-Messung mit veraltetem Code, neu = Re-Run mit gefixtem Code)

| # | Karte | v1-Messung (veraltet) | v2fix-Messung (2026-07-26) | Urteil |
|---|---|---|---|---|
| 1 | H1-Breaker Retest (v1 M15-Conf / v2 M1-Trigger) | −0.62R / −0.49R | −0.62R (identisch) / −0.57R @2+5 (Entries 217→149, Lookahead-Fix) | tot |
| 2 | H1-Fakeout-Flip (Reclaim) | −0.51R | nicht erneut gemessen (unveränderte Code-Pfade) | tot |
| 3 | H4-Breaker Retest | −0.47R (n=43) | **identisch reproduziert** (−0.47R, n=43) | tot |
| 4 | H4-Flip | −0.05R (brutto 0R) | **identisch reproduziert** | tot |
| 5a | Seeker-Kill Retest (Variante A) | −0.08R | **identisch reproduziert** | tot |
| 5b | Seeker-Kill Direkt-Entry (Variante B) | −1.07R | **+0.20R (2R) / +0.44R (opp. Swing)** — Stop-Guard: 143/222 Entries als `non_protective_stop` verworfen, n=79/76 | **zweiter positiver Hinweis**, aber n<100 |
| 6 | H4-Flip + Delta-Selektion | −0.02R | nicht erneut gemessen (Selektion unverändert) | tot (Filter tautologisch, 91 %) |
| 7 | H4-Continuation (BOS + ATR-Stop/Trail) | −0.08R / Trail −0.17R | fixe Ziele identisch; Trail −0.12R (Closed-Bar-Regel) | tot |
| 8 | H4-Flip + Funding-Selektion | **+0.04R** (n=94, CI inkl. Breakeven) | nicht erneut gemessen (Funding-Selektion unverändert, nur validate-Fix) | formal tot, positiver Hinweis |
| 9 | OI-konditionierter Flip | — | — | übersprungen: OI-History < 30 Tage |

**Neuer Befund aus dem Re-Run (5b):** Die Stop-Guard (`non_protective_stop`,
Dokumentation in `backtest_seeker.py`) entfernt die strukturell kaputten
Direkt-Entries (Stop jenseits Entry oder Risiko < 2× Round-Trip-Kosten).
Danach zeigt der Direkt-Einstieg am Kill-Schluss **+0.195R (2R, n=79)** und
**+0.444R (Gegenseiten-Swing, n=76, Wilson-CI [0.317, 0.533] schließt
Breakeven 0.308 knapp aus)**. Das erfüllt formal das +0.2R-Kriterium am
Gegenseiten-Target, aber n=76 < 100 → kein OOS-validate, kein Echtgeld.
Priorität für die nächste Datenperiode: n wachsen lassen, dann EIN validate.

## Reproduktion

Alle Reports enthalten seit Commit `1f8e4f8` einen `run_meta`-Block
(Skript, argv, git_commit, timestamp, db_path, table_rows, train_fraction).
Die v2fix-Messung lief am 2026-07-26 auf dem Windows-Host read-only gegen
`C:\edgerunner\edgerunner.db` (candles_4h: 3.300, candles_1h: 13.200 Zeilen)
mit dem Code-Stand `1f8e4f8` + den Fixes dieser Runde (uncommitted zum
Messzeitpunkt, werden im selben Commit eingereicht). Hinweis: `run_meta.git_commit`
auf dem Windows-Host zeigt den dortigen Repo-Checkout (`7f7086c`), weil die
Skripte per scp verteilt werden — maßgeblich ist der Commit dieses Repos.

Kommandos (Windows, read-only, aus C:\edgerunner):

```
python backtest_breaker.py --db C:\edgerunner\edgerunner.db --mode diagnose --strategy v1 --setup-tf 4h --exec-tf 1h --fee-bps 5 --slippage-bps 5 --json-out diag_h4_breaker_v2fix.json --md-out diag_h4_breaker_v2fix.md
python backtest_breaker.py --db C:\edgerunner\edgerunner.db --mode diagnose --strategy v2 --fee-bps 5 --slippage-bps 5 --json-out diag_breaker_v2fix.json --md-out diag_breaker_v2fix.md
python backtest_flip.py   --db C:\edgerunner\edgerunner.db --mode diagnose --setup-tf 4h --exec-tf 1h --json-out diag_h4_flip_v2fix.json --md-out diag_h4_flip_v2fix.md
python backtest_seeker.py --db C:\edgerunner\edgerunner.db --mode diagnose --json-out diag_seeker_v2fix.json --md-out diag_seeker_v2fix.md
python backtest_trend.py  --db C:\edgerunner\edgerunner.db --mode diagnose --json-out diag_trend_v2fix.json --md-out diag_trend_v2fix.md
```

Reports: `C:\edgerunner\diag_*_v2fix.{json,md}`, Kopien in
`~/edgerunner/.runtime/`. Die drei identisch reproduzierten Karten (3, 4,
5a) belegen, dass Maschinerie und Datenbestand stabil sind; alle Deltas
sind einzelnen dokumentierten Fixes zuordenbar (Lookahead v2, Stop-Guard
Seeker-B, Chandelier-Ordnung Trend).

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
- Zwei positive Hinweise nach den Fixes: **Funding-Selektion** (+0.04R,
  n=94) und jetzt **Seeker-Kill Direkt mit Stop-Guard** (+0.20R/+0.44R,
  n=76–79). Beide unter dem n≥100-Validate-Kriterium — gemeinsame Antwort:
  n wachsen lassen (Collector läuft, Markt liefert wöchentlich neue
  Kills/Breaks), dann jeweils EIN validate-Lauf pro Karte.
- Der Whale-Collector (seit 2026-07-24 live) sammelt genau diese
  Zusatzinformation. In 2–3 Monaten: Funding + Whale als zwei
  Selektionsebenen auf der H4-Flip-Karte nachmessen.
- Bis dahin bleibt Echtgeld tabu. Paper-Pipeline (Funding-Join, Selektion,
  Order-Sim) darf gebaut werden — nicht weil +0.04R tradbar ist, sondern
  um live n zu sammeln.

## Maschinerie (das eigentliche Asset)

`backtest_{breaker,flip,seeker,trend}.py` — TF-generisch, PIT-sicher,
Kosten-ehrlich, Wilson-Disziplin, 39 Unit-Tests. Falsifiziert jede neue
Karte in < 1 min pro Messung. `funding_loader.py` + `funding.db`
(separat, 1712 Prints seit 2025-01).
