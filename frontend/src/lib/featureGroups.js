// 13 Feature Groups — 89 features total
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
    features: ['bos_body', 'bos_wick', 'break_depth', 'swing_age', 'swing_age_norm', 'breaks_highs', 'breaks_lows', 'max_age_broken', 'min_age_broken'],
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
    features: ['is_seeker_hs', 'is_seeker_ls', 'is_seeker_div', 'seeker_div_nr', 'dist_prev_seeker_div', 'dist_prev_seeker_div_norm', 'is_seeker_kill', 'killed_seeker_divs', 'candle_was_seeker', 'candle_was_seeker_div'],
  },
  {
    id: 'context',
    name: 'Context/Trend',
    color: '#4488ff',
    features: ['ema21_dist', 'ema50_dist', 'ema200_dist', 'atr14', 'rsi14', 'vwap_dist'],
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

// ── Feature Metadata: desc + tier (1=Quick Pick, 2=Useful, 3=Advanced) ──

export const FEATURE_META = {
  // Tier 1 — Quick Picks (12)
  bos_bull:          { desc: 'Bullish Break of Structure — Preis bricht ueber letztes Swing High', tier: 1 },
  bos_bear:          { desc: 'Bearish Break of Structure — Preis bricht unter letztes Swing Low', tier: 1 },
  choch:             { desc: 'Change of Character — Trendwechsel-Signal', tier: 1 },
  is_bullish:        { desc: 'Gruene Kerze (Close > Open)', tier: 1 },
  bull_div:          { desc: 'Bullische Divergenz — Preis faellt, MACD steigt', tier: 1 },
  bear_div:          { desc: 'Baerische Divergenz — Preis steigt, MACD faellt', tier: 1 },
  rsi14:             { desc: 'RSI(14) — <30 ueberverkauft, >70 ueberkauft', tier: 1 },
  vol_vs_ma:         { desc: 'Volume vs Durchschnitt — >1.5 = hohes Volumen', tier: 1 },
  ema21_dist:        { desc: 'Abstand zur EMA21 in % — positiv = drueber', tier: 1 },
  break_depth:       { desc: 'Tiefe des Struktur-Bruchs (wie weit ueber/unter Swing)', tier: 1 },
  is_seeker_kill:    { desc: 'Seeker Kill Signal — Seeker-Div wurde invalidiert', tier: 1 },
  whale_sentiment:   { desc: 'Whale Kauf-/Verkaufsdruck — positiv = bullish', tier: 1 },

  // Tier 2 — Useful (18)
  is_swing_high:     { desc: 'Kerze ist ein Swing High', tier: 2 },
  is_swing_low:      { desc: 'Kerze ist ein Swing Low', tier: 2 },
  body_ratio:        { desc: 'Body-Anteil an Total Range (0-1)', tier: 2 },
  wick_ratio:        { desc: 'Upper Wick / Lower Wick Verhaeltnis', tier: 2 },
  delta_pct:         { desc: 'Delta in % der Range — Kauf- vs Verkaufsdruck', tier: 2 },
  swing_age_norm:    { desc: 'Normalisiertes Alter des gebrochenen Swings (0-1)', tier: 2 },
  div_strength:      { desc: 'Staerke der Divergenz (MACD-Differenz)', tier: 2 },
  ema50_dist:        { desc: 'Abstand zur EMA50 in %', tier: 2 },
  ema200_dist:       { desc: 'Abstand zur EMA200 in %', tier: 2 },
  atr14:             { desc: 'Average True Range (14) — Volatilitaet', tier: 2 },
  vwap_dist:         { desc: 'Abstand zum VWAP in %', tier: 2 },
  is_seeker_div:     { desc: 'Seeker Divergenz aktiv', tier: 2 },
  htf_trend:         { desc: 'Hoehere Timeframe Trend-Richtung', tier: 2 },
  htf_bos:           { desc: 'Break of Structure auf hoeherer Timeframe', tier: 2 },
  whale_confidence:  { desc: 'Konfidenz des Whale-Signals (0-1)', tier: 2 },
  bull_pressure:     { desc: 'Whale Kaufdruck (aggregiert)', tier: 2 },
  bear_pressure:     { desc: 'Whale Verkaufsdruck (aggregiert)', tier: 2 },
  elite_whale_active: { desc: 'Elite-Whale (>$1M) ist aktiv', tier: 2 },

  // Tier 3 — Advanced (rest)
  body_size:         { desc: 'Absolute Body-Groesse in Preiseinheiten', tier: 3 },
  upper_wick:        { desc: 'Oberer Docht in Preiseinheiten', tier: 3 },
  lower_wick:        { desc: 'Unterer Docht in Preiseinheiten', tier: 3 },
  total_range:       { desc: 'High - Low der Kerze', tier: 3 },
  body_position:     { desc: 'Position des Body innerhalb der Range (0-1)', tier: 3 },
  delta_vs_ma:       { desc: 'Delta vs Durchschnitts-Delta', tier: 3 },
  dist_swing_high:   { desc: 'Abstand zum naechsten Swing High', tier: 3 },
  dist_swing_low:    { desc: 'Abstand zum naechsten Swing Low', tier: 3 },
  bos_body:          { desc: 'Break of Structure durch Body (nicht nur Wick)', tier: 3 },
  bos_wick:          { desc: 'Break of Structure durch Wick', tier: 3 },
  swing_age:         { desc: 'Alter des gebrochenen Swings in Kerzen', tier: 3 },
  breaks_highs:      { desc: 'Anzahl gebrochener Swing Highs', tier: 3 },
  breaks_lows:       { desc: 'Anzahl gebrochener Swing Lows', tier: 3 },
  max_age_broken:    { desc: 'Maximales Alter unter gebrochenen Swings', tier: 3 },
  min_age_broken:    { desc: 'Minimales Alter unter gebrochenen Swings', tier: 3 },
  sw_body_ratio:     { desc: 'Body-Verhaeltnis des gebrochenen Swings', tier: 3 },
  sw_wick_ratio:     { desc: 'Wick-Verhaeltnis des gebrochenen Swings', tier: 3 },
  sw_delta_pct:      { desc: 'Delta % des gebrochenen Swings', tier: 3 },
  sw_vol_rel:        { desc: 'Relatives Volumen des gebrochenen Swings', tier: 3 },
  sw_bullish:        { desc: 'Gebrochener Swing war bullish', tier: 3 },
  sw_body_pos:       { desc: 'Body-Position des gebrochenen Swings', tier: 3 },
  vol_ratio_bsw:     { desc: 'Volumen-Ratio Break vs Swing', tier: 3 },
  delta_ratio_bsw:   { desc: 'Delta-Ratio Break vs Swing', tier: 3 },
  body_ratio_bsw:    { desc: 'Body-Ratio Break vs Swing', tier: 3 },
  same_dir:          { desc: 'Break und Swing gleiche Richtung', tier: 3 },
  broken_was_seeker: { desc: 'Gebrochener Swing war Seeker', tier: 3 },
  broken_was_seeker_div: { desc: 'Gebrochener Swing war Seeker-Div', tier: 3 },
  swing_had_break:   { desc: 'Swing hatte vorherigen Break', tier: 3 },
  chain_depth:       { desc: 'Tiefe der Break-Chain', tier: 3 },
  cluster_range:     { desc: 'Range des Swing-Clusters in Preiseinheiten', tier: 3 },
  cluster_range_atr: { desc: 'Cluster-Range normalisiert auf ATR', tier: 3 },
  cluster_spread:    { desc: 'Spread des Clusters', tier: 3 },
  macd_line:         { desc: 'MACD Linienwert', tier: 3 },
  macd_peak:         { desc: 'MACD ist an einem Peak', tier: 3 },
  macd_trough:       { desc: 'MACD ist an einem Trough', tier: 3 },
  div_near_daily:    { desc: 'Divergenz nahe Daily-Level', tier: 3 },
  div_width:         { desc: 'Breite der Divergenz in Kerzen', tier: 3 },
  is_seeker_hs:      { desc: 'Seeker Higher-Setup Signal', tier: 3 },
  is_seeker_ls:      { desc: 'Seeker Lower-Setup Signal', tier: 3 },
  seeker_div_nr:     { desc: 'Nummer der Seeker-Divergenz', tier: 3 },
  dist_prev_seeker_div: { desc: 'Abstand zur vorherigen Seeker-Div', tier: 3 },
  dist_prev_seeker_div_norm: { desc: 'Normalisierter Abstand zur vorherigen Seeker-Div', tier: 3 },
  killed_seeker_divs: { desc: 'Anzahl gekillter Seeker-Divs', tier: 3 },
  candle_was_seeker: { desc: 'Diese Kerze war ein Seeker', tier: 3 },
  candle_was_seeker_div: { desc: 'Diese Kerze war eine Seeker-Div', tier: 3 },
  htf_swing_high:    { desc: 'HTF Swing High aktiv', tier: 3 },
  htf_swing_low:     { desc: 'HTF Swing Low aktiv', tier: 3 },
  whale_cluster:     { desc: 'Whale-Cluster erkannt', tier: 3 },
  whale_cluster_strength: { desc: 'Staerke des Whale-Clusters', tier: 3 },
  whale_cluster_dir: { desc: 'Richtung des Whale-Clusters', tier: 3 },
}

// Quick-access lists by tier
export const TIER1_FEATURES = Object.entries(FEATURE_META).filter(([, m]) => m.tier === 1).map(([k]) => k)
export const TIER2_FEATURES = Object.entries(FEATURE_META).filter(([, m]) => m.tier === 2).map(([k]) => k)
export const TIER3_FEATURES = Object.entries(FEATURE_META).filter(([, m]) => m.tier === 3).map(([k]) => k)
