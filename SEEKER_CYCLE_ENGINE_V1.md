# Seeker Cycle Engine v1

## Ziel

`Seeker`-Signale sollen nicht mehr nur als einzelne Candle-Flags gelesen werden.  
Sie sollen als laufende `Cycles` modelliert werden:

- wo ein Cycle entstanden ist
- welche Seite er ist (`HS` / `LS`)
- wie alt er ist
- wie oft und wie lange der Markt daran gearbeitet hat
- wann Divs, Kills, Retests und Reclaims passiert sind
- wie der Cycle in andere Timeframes eingebettet ist

Das Ziel ist nicht ein weiterer Einzelindikator, sondern eine echte `Zone State Machine`.

## Kernidee

Ein `Seeker` startet einen Cycle.

Danach kann dieser Cycle:

- offen bleiben
- Divs sammeln
- mehrfach angegriffen werden
- gekillt werden
- später retestet werden
- reclaimed werden
- invalidiert werden

Gleichzeitig können innerhalb eines bestehenden Cycles neue Seeker entstehen.  
Deshalb ist das Modell nicht nur linear pro Candle, sondern ein Graph aus aktiven und historischen Zonen.

## V1-Objekte

### 1. `seeker_cycle`

Ein Cycle beschreibt eine einzelne ursprüngliche Seeker-Zone.

Pflichtfelder:

- `cycle_id`
- `tf`
- `type` = `HS` | `LS`
- `origin_ts`
- `origin_index`
- `origin_price_high`
- `origin_price_low`
- `origin_open`
- `origin_close`
- `zone_top`
- `zone_bottom`
- `zone_size`
- `zone_pct_of_range`
- `zone_vs_body`
- `wick_dominance`
- `origin_body_ratio`
- `origin_wick_ratio`
- `origin_body_position`
- `status`

### 2. `cycle_event`

Jedes relevante Ereignis eines Cycles.

Typen:

- `origin`
- `div`
- `kill`
- `retest`
- `reclaim`
- `invalidation`
- `touch`

Pflichtfelder:

- `cycle_id`
- `event_type`
- `tf`
- `ts`
- `candle_ts`
- `distance_from_origin_bars`
- `distance_from_origin_ms`
- `meta_json`

### 3. `cycle_relation`

Verknüpfungen zwischen Cycles.

Beispiele:

- `child_of`
- `overlaps`
- `inside_zone_of`
- `kills`
- `retests`
- `conflicts_with`
- `aligns_with`

## Cycle-Status

Ein Cycle soll immer genau einen Hauptstatus haben:

- `open`
- `under_attack`
- `div_active`
- `killed`
- `retested`
- `reclaimed`
- `invalidated`
- `spent`

Zusätzlich kann es Hilfsflags geben:

- `fresh`
- `mature`
- `stale`
- `clustered`
- `mtf_aligned`
- `mtf_conflicted`

## Wichtige Metriken

### Alters- und Reife-Metriken

- `cycle_age_bars`
- `cycle_age_ms`
- `time_to_first_div_bars`
- `time_to_first_div_ms`
- `time_to_kill_bars`
- `time_to_kill_ms`
- `time_since_kill_bars`
- `time_since_kill_ms`
- `time_to_retest_bars`
- `time_to_retest_ms`
- `time_to_reclaim_bars`
- `time_to_reclaim_ms`

### Druck-/Bearbeitungs-Metriken

- `div_count_total`
- `div_count_hs`
- `div_count_ls`
- `div_spacing_avg_bars`
- `div_spacing_avg_ms`
- `div_spacing_min_bars`
- `div_spacing_max_bars`
- `kill_attempt_density`
- `touch_count`
- `touch_density`

### Zonen-Metriken

- `zone_size_points`
- `zone_size_atr`
- `zone_pct_of_origin_range`
- `zone_vs_origin_body`
- `zone_shape_score`

### Ergebnis-/Auflösungs-Metriken

- `resolution_type`
  - `fakeout`
  - `true_breakout`
  - `failed_breakout`
  - `acceptance`
  - `rejection`
  - `absorption`
- `resolution_latency_bars`
- `resolution_latency_ms`

## Candle-Kontext um den Cycle

Der Cycle alleine reicht nicht.  
Jeder relevante Event-Punkt braucht den lokalen Candle-Kontext.

Pflichtfenster:

- `3 Kerzen vor origin`
- `3 Kerzen nach origin`
- `3 Kerzen vor div`
- `3 Kerzen nach div`
- `3 Kerzen vor kill`
- `3 Kerzen nach kill`
- `3 Kerzen vor retest`
- `3 Kerzen nach retest`

Zu lesen sind dort mindestens:

- `bos_bull`, `bos_bear`, `choch`
- `bos_body`, `bos_wick`
- `breaks_highs`, `breaks_lows`
- `max_age_broken`, `min_age_broken`
- `bull_div`, `bear_div`, `bull_div_streak`, `bear_div_streak`
- `is_seeker_div_hs`, `is_seeker_div_ls`
- `is_seeker_kill`
- `spot_volume`, `spot_delta`
- `futures_volume`, `futures_delta`
- `futures_minus_spot_volume`, `futures_minus_spot_delta`
- `vol_vs_ma`, `delta_pct`
- `whale_*`

## Timeframe-übergreifende Sicht

Jeder Cycle soll Teil einer `MTF Zone Map` sein.

Fragen:

- Welcher offene `HS`-Cycle liegt über dem Preis?
- Welcher offene `LS`-Cycle liegt unter dem Preis?
- Welche gekillte Zone wurde gerade retestet?
- In welchem TF befindet sich der Markt gerade innerhalb eines offenen Cycles?
- Laufen untere TFs in einen höheren offenen Cycle hinein?
- Wurde ein lokaler Kill gegen einen höheren offenen Cycle gespielt?

V1 soll mindestens liefern:

- `nearest_open_hs_above`
- `nearest_open_ls_below`
- `nearest_killed_hs_above`
- `nearest_killed_ls_below`
- `distance_to_zone_points`
- `distance_to_zone_atr`
- `distance_to_zone_pct`
- `mtf_alignment_score`
- `mtf_conflict_score`

## Beispielzustände, die das System erkennen soll

### 1. Offene große LS-Zone, Markt hängt darin

- große `LS`-Zone
- mehrere Divs
- keine klare Acceptance zurück nach oben
- Ergebnis: `decision zone`, nicht automatisch bullish

### 2. LS-Kill -> Retest -> Fortsetzung short

- `LS` wird gekillt
- Preis kehrt in kill-Zone zurück
- Retest scheitert
- neuer `bos_bear`
- Ergebnis: `continuation after failed reclaim`

### 3. LS-Kill nur als Fake Move

- `LS` wird gekillt
- Preis kehrt schnell zurück
- Reclaim kommt sofort
- Struktur dreht
- Ergebnis: `failed kill / fake move`

### 4. Mehrere Seeker-Divs in enger Zone

- viele Divs in kurzer Zeit
- enger Preisbereich
- höherer TF darüber offen
- Ergebnis: `cluster / loaded liquidity zone`

## Was V1 noch nicht sein muss

Nicht direkt in v1:

- vollautomatische Trade-Regeln
- perfektes Labeling aller Resolution-Types
- vollständige Whale-/Liquidation-Historie
- grafische Zone-Heatmap
- vollständige Cycle-ID-Vererbung über alle historischen Daten seit Tag 1

V1 soll zuerst:

- Cycles sauber modellieren
- Zeit und Status messen
- MTF-Zonen verknüpfen
- Team Room und Live Analysis damit füttern

## Integration in Edgerunner

### Analyzer

Der Candle Analyzer bleibt die Rohquelle.

Er liefert weiter Candle-Features wie:

- BOS / CHoCH
- MACD-Divs
- Seeker / Seeker-Div / Kill
- Volumen / Delta / Spot / Futures
- Whale-Kontext

Die `Seeker Cycle Engine` sitzt darüber.

### DB

V1 sollte nicht alles in die Candle-Tabellen pressen.

Neue Tabellen:

- `seeker_cycles`
- `seeker_cycle_events`
- `seeker_cycle_relations`
- optional später `seeker_cycle_snapshots`

### Team Room

Der Team Room soll später sagen können:

- in welchem offenen Cycle der Markt gerade steckt
- wie alt dieser Cycle ist
- wie viele Divs er gesammelt hat
- ob er frisch, reif oder ausgebrannt ist
- welche nächste offene oder gekillte Zone darüber/darunter liegt

### Live Analysis

Live Analysis soll nicht nur rohe Candle-Features zeigen, sondern:

- `active cycle context`
- `nearest zone map`
- `fresh div / repeated div / retest / reclaim`

## Erste Implementierungsphasen

### Phase 1

- `cycle_id`
- Cycle-Ursprung speichern
- `HS/LS`
- Zone-Geometrie
- Basisstatus `open/killed`

### Phase 2

- Div- und Kill-Events je Cycle
- Alters- und Reife-Metriken
- Retest-/Reclaim-Events

### Phase 3

- MTF Zone Map
- Distanz zur nächsten offenen/gekillten Zone
- Alignment/Conflict zwischen TFs

### Phase 4

- Team Room / Live Analysis mit Cycle-Kontext
- Research Cases auf Cycle-Zuständen statt nur auf Candle-Flags

## Leitgedanke

`Seeker` ist kein Entry-Signal.

`Seeker` ist der Start einer Zustandsmaschine.

Wenn wir diese Zyklen sauber modellieren, wissen wir nicht nur, was die aktuelle Candle macht, sondern wo der Markt gerade in seiner Liquiditätsgeschichte steht.
