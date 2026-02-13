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
