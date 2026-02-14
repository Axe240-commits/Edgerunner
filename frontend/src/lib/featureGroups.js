// 12 Feature Groups — 90 features total (context group removed)
// Each group has a name, color, and list of feature keys

const GROUPS = [
  {
    id: 'raw',
    name: 'Raw Data',
    color: '#8888aa',
    features: ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta'],
  },
  {
    id: 'anatomy',
    name: 'Candle Anatomy',
    color: '#00f0ff',
    features: ['body_size', 'upper_wick', 'lower_wick', 'total_range', 'body_ratio', 'wick_ratio', 'body_position', 'is_bullish'],
  },
  {
    id: 'volume',
    name: 'Volume/Delta',
    color: '#00ff88',
    features: ['delta_pct', 'vol_vs_ma', 'delta_vs_ma'],
  },
  {
    id: 'swing',
    name: 'Swing Structure',
    color: '#ff3355',
    features: ['is_swing_high', 'is_swing_low', 'bos_bull', 'bos_bear', 'choch', 'dist_swing_high', 'dist_swing_low'],
  },
  {
    id: 'break',
    name: 'Break Quality',
    color: '#ff00ff',
    features: ['bos_body', 'bos_wick', 'break_depth', 'swing_age', 'broken_swing_ts', 'swing_age_norm', 'breaks_highs', 'breaks_lows', 'max_age_broken', 'min_age_broken'],
  },
  {
    id: 'pair',
    name: 'Paarung',
    color: '#ffd700',
    features: ['sw_body_ratio', 'sw_wick_ratio', 'sw_delta_pct', 'sw_vol_rel', 'sw_bullish', 'sw_body_pos', 'sw_ohlc', 'vol_ratio_bsw', 'delta_ratio_bsw', 'body_ratio_bsw', 'same_dir', 'broken_was_seeker', 'broken_was_seeker_div'],
  },
  {
    id: 'chain',
    name: 'Chain',
    color: '#ffaa00',
    features: ['swing_had_break', 'chain_depth', 'prev_swing_features'],
  },
  {
    id: 'cluster',
    name: 'Cluster',
    color: '#ff6600',
    features: ['cluster_range', 'cluster_range_atr', 'cluster_spread'],
  },
  {
    id: 'macd',
    name: 'MACD + Div',
    color: '#aa44ff',
    features: ['macd_line', 'macd_peak', 'macd_trough', 'bull_div', 'bear_div', 'div_near_daily', 'div_strength', 'div_width'],
  },
  {
    id: 'seeker',
    name: 'Seeker',
    color: '#44ffaa',
    features: ['is_seeker_hs', 'is_seeker_ls', 'is_seeker_div', 'seeker_div_nr', 'dist_prev_seeker_div', 'dist_prev_seeker_div_norm', 'is_seeker_kill', 'killed_seeker_divs', 'killed_seeker_ts', 'candle_was_seeker', 'candle_was_seeker_div'],
  },
  {
    id: 'htf',
    name: 'Multi-TF',
    color: '#88ccff',
    features: ['htf_trend', 'htf_swing_high', 'htf_swing_low', 'htf_bos'],
  },
  {
    id: 'whale',
    name: 'Whale',
    color: '#ff44aa',
    features: ['whale_sentiment', 'whale_confidence', 'bull_pressure', 'bear_pressure', 'whale_cluster', 'whale_cluster_strength', 'whale_cluster_dir', 'elite_whale_active'],
  },
]

export default GROUPS

// Flat lookup: feature name -> group
export const featureToGroup = {}
for (const g of GROUPS) {
  for (const f of g.features) {
    featureToGroup[f] = g
  }
}

export const ALL_FEATURE_NAMES = GROUPS.flatMap(g => g.features)

// Context labels for UI
export const CONTEXT_LABELS = {
  meta:    { label: 'Meta-Kerze', color: '#ffaa00' },
  swing:   { label: 'Swing-Kerze', color: '#ff3355' },
  pair:    { label: 'Vergleich', color: '#aa44ff' },
  chain:   { label: 'Kette', color: '#556677' },
  cluster: { label: 'Cluster', color: '#556677' },
  general: { label: 'Markt', color: '#556677' },
}

// ── Feature Metadata: desc + tier + context ──

export const FEATURE_META = {
  // Swing Structure
  bos_bull:          { desc: 'Meta-Kerze bricht Swing High nach oben (bullish)', tier: 1, context: 'meta' },
  bos_bear:          { desc: 'Meta-Kerze bricht Swing Low nach unten (bearish)', tier: 1, context: 'meta' },
  choch:             { desc: 'Trendwechsel — erster Break gegen vorherigen Trend', tier: 1, context: 'meta' },
  is_bullish:        { desc: 'Gruene Kerze (Close > Open)', tier: 1, context: 'meta' },
  is_swing_high:     { desc: 'Diese Kerze ist ein Swing High', tier: 2, context: 'meta' },
  is_swing_low:      { desc: 'Diese Kerze ist ein Swing Low', tier: 2, context: 'meta' },
  dist_swing_high:   { desc: 'Abstand zum naechsten Swing High (ATR-normalisiert)', tier: 3, context: 'meta' },
  dist_swing_low:    { desc: 'Abstand zum naechsten Swing Low (ATR-normalisiert)', tier: 3, context: 'meta' },

  // Volume/Delta
  vol_vs_ma:         { desc: 'Volumen vs Durchschnitt letzte 10 Kerzen — >1.5 = Spike', tier: 1, context: 'meta' },
  delta_pct:         { desc: 'Delta = Kauf minus Verkauf, als % des Volumens', tier: 2, context: 'meta' },
  delta_vs_ma:       { desc: 'Absolutes Delta vs Durchschnitts-Delta', tier: 3, context: 'meta' },

  // Candle Anatomy
  body_ratio:        { desc: 'Body-Anteil an Total Range (0-1)', tier: 2, context: 'meta' },
  wick_ratio:        { desc: 'Oberer Docht / Unterer Docht Verhaeltnis', tier: 2, context: 'meta' },
  body_position:     { desc: 'Position des Close innerhalb der Range (0=Low, 1=High)', tier: 3, context: 'meta' },
  body_size:         { desc: 'Absolute Body-Groesse in Preiseinheiten', tier: 3, context: 'meta' },
  upper_wick:        { desc: 'Oberer Docht in Preiseinheiten', tier: 3, context: 'meta' },
  lower_wick:        { desc: 'Unterer Docht in Preiseinheiten', tier: 3, context: 'meta' },
  total_range:       { desc: 'High - Low der Kerze', tier: 3, context: 'meta' },

  // Break Quality
  break_depth:       { desc: 'Wie weit die Meta-Kerze ueber/unter das Swing-Level gebrochen hat', tier: 1, context: 'meta' },
  bos_body:          { desc: 'Break geschah mit dem Body (stark, nicht nur Docht)', tier: 3, context: 'meta' },
  bos_wick:          { desc: 'Break geschah mit dem Docht (schwaecher)', tier: 3, context: 'meta' },
  swing_age:         { desc: 'Alter des gebrochenen Swings in Kerzen-Abstand', tier: 3, context: 'meta' },
  broken_swing_ts:   { desc: 'Timestamp der gebrochenen Swing-Kerze', tier: 3, context: 'swing' },
  swing_age_norm:    { desc: 'Normalisiertes Alter des gebrochenen Swings (0-1)', tier: 3, context: 'meta' },
  breaks_highs:      { desc: 'Anzahl gleichzeitig gebrochener Swing Highs', tier: 3, context: 'meta' },
  breaks_lows:       { desc: 'Anzahl gleichzeitig gebrochener Swing Lows', tier: 3, context: 'meta' },
  max_age_broken:    { desc: 'Aeltester gebrochener Swing (Kerzen-Abstand)', tier: 3, context: 'meta' },
  min_age_broken:    { desc: 'Juengster gebrochener Swing (Kerzen-Abstand)', tier: 3, context: 'meta' },

  // Paarung — Swing-Kerze Eigenschaften
  sw_body_ratio:     { desc: 'Body-Anteil der gebrochenen Swing-Kerze (0-1)', tier: 3, context: 'swing' },
  sw_wick_ratio:     { desc: 'Docht-Verhaeltnis der gebrochenen Swing-Kerze', tier: 3, context: 'swing' },
  sw_delta_pct:      { desc: 'Delta % der gebrochenen Swing-Kerze', tier: 3, context: 'swing' },
  sw_vol_rel:        { desc: 'Volumen der Swing-Kerze relativ zur Meta-Kerze', tier: 3, context: 'swing' },
  sw_bullish:        { desc: 'Gebrochene Swing-Kerze war bullish (gruen)', tier: 3, context: 'swing' },
  sw_body_pos:       { desc: 'Body-Position der gebrochenen Swing-Kerze', tier: 3, context: 'swing' },
  broken_was_seeker: { desc: 'Gebrochene Swing-Kerze war ein Seeker', tier: 3, context: 'swing' },
  broken_was_seeker_div: { desc: 'Gebrochene Swing-Kerze war eine Seeker-Divergenz', tier: 3, context: 'swing' },

  // Paarung — Vergleich Meta vs Swing
  vol_ratio_bsw:     { desc: 'Volumen Meta-Kerze / Volumen Swing-Kerze', tier: 3, context: 'pair' },
  delta_ratio_bsw:   { desc: 'Delta Meta-Kerze / Delta Swing-Kerze', tier: 3, context: 'pair' },
  body_ratio_bsw:    { desc: 'Body Meta-Kerze / Body Swing-Kerze', tier: 3, context: 'pair' },
  same_dir:          { desc: 'Meta-Kerze und Swing-Kerze gleiche Richtung', tier: 3, context: 'pair' },

  // Kette
  swing_had_break:   { desc: 'Die gebrochene Swing-Kerze hatte selbst einen Break', tier: 3, context: 'chain' },
  chain_depth:       { desc: 'Tiefe der rekursiven Break-Kette', tier: 3, context: 'chain' },

  // Cluster
  cluster_range:     { desc: 'Range des Swing-Clusters in Preiseinheiten', tier: 3, context: 'cluster' },
  cluster_range_atr: { desc: 'Cluster-Range normalisiert auf ATR', tier: 3, context: 'cluster' },
  cluster_spread:    { desc: 'Zeitliche Spanne des Clusters in Kerzen', tier: 3, context: 'cluster' },

  // MACD + Divergenzen
  bull_div:          { desc: 'Bullische MACD-Divergenz — Preis faellt aber MACD steigt', tier: 1, context: 'general' },
  bear_div:          { desc: 'Baerische MACD-Divergenz — Preis steigt aber MACD faellt', tier: 1, context: 'general' },
  div_strength:      { desc: 'Staerke der Divergenz (MACD-Differenz)', tier: 2, context: 'general' },
  macd_line:         { desc: 'MACD Linienwert (5/13/1)', tier: 3, context: 'general' },
  macd_peak:         { desc: 'MACD ist an einem Peak (positiver Bereich)', tier: 3, context: 'general' },
  macd_trough:       { desc: 'MACD ist an einem Trough (negativer Bereich)', tier: 3, context: 'general' },
  div_near_daily:    { desc: 'Divergenz nahe Daily High/Low', tier: 3, context: 'general' },
  div_width:         { desc: 'Breite der Divergenz in Kerzen', tier: 3, context: 'general' },

  // Seeker
  is_seeker_kill:    { desc: 'Seeker-Zyklus wurde durch BOS zerstoert', tier: 1, context: 'meta' },
  is_seeker_hs:      { desc: 'Kerze ist ein High Seeker (Swing High + grosser oberer Docht)', tier: 3, context: 'meta' },
  is_seeker_ls:      { desc: 'Kerze ist ein Low Seeker (Swing Low + grosser unterer Docht)', tier: 3, context: 'meta' },
  is_seeker_div:     { desc: 'Seeker-Divergenz aktiv auf dieser Kerze', tier: 2, context: 'meta' },
  seeker_div_nr:     { desc: 'Nummer der Seeker-Divergenz in diesem Zyklus', tier: 3, context: 'meta' },
  dist_prev_seeker_div: { desc: 'Abstand zur vorherigen Seeker-Divergenz in Kerzen', tier: 3, context: 'meta' },
  dist_prev_seeker_div_norm: { desc: 'Normalisierter Abstand zur vorherigen Seeker-Div (0-1)', tier: 3, context: 'meta' },
  killed_seeker_divs: { desc: 'Anzahl gekillter Seeker-Divergenzen bei diesem Kill', tier: 3, context: 'meta' },
  killed_seeker_ts: { desc: 'Timestamp des getöteten Seekers', tier: 3, context: 'meta' },
  candle_was_seeker: { desc: 'Diese Kerze war Ursprung eines Seeker-Zyklus', tier: 3, context: 'meta' },
  candle_was_seeker_div: { desc: 'Diese Kerze war eine Seeker-Divergenz', tier: 3, context: 'meta' },

  // Multi-TF
  htf_trend:         { desc: 'Hoehere Timeframe Trend-Richtung', tier: 2, context: 'general' },
  htf_bos:           { desc: 'Break of Structure auf hoeherer Timeframe', tier: 2, context: 'general' },
  htf_swing_high:    { desc: 'HTF Swing High Preis', tier: 3, context: 'general' },
  htf_swing_low:     { desc: 'HTF Swing Low Preis', tier: 3, context: 'general' },

  // Whale
  whale_sentiment:   { desc: 'Whale Kauf-/Verkaufsdruck — positiv = bullish', tier: 1, context: 'general' },
  whale_confidence:  { desc: 'Konfidenz des Whale-Signals (0-1)', tier: 2, context: 'general' },
  bull_pressure:     { desc: 'Whale Kaufdruck (aggregiert)', tier: 2, context: 'general' },
  bear_pressure:     { desc: 'Whale Verkaufsdruck (aggregiert)', tier: 2, context: 'general' },
  elite_whale_active: { desc: 'Elite-Whale (>$1M) ist aktiv', tier: 2, context: 'general' },
  whale_cluster:     { desc: 'Whale-Cluster erkannt', tier: 3, context: 'general' },
  whale_cluster_strength: { desc: 'Staerke des Whale-Clusters', tier: 3, context: 'general' },
  whale_cluster_dir: { desc: 'Richtung des Whale-Clusters', tier: 3, context: 'general' },
}

// Quick-access lists by tier
export const TIER1_FEATURES = Object.entries(FEATURE_META).filter(([, m]) => m.tier === 1).map(([k]) => k)
export const TIER2_FEATURES = Object.entries(FEATURE_META).filter(([, m]) => m.tier === 2).map(([k]) => k)
export const TIER3_FEATURES = Object.entries(FEATURE_META).filter(([, m]) => m.tier === 3).map(([k]) => k)
