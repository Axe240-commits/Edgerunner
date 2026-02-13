import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, apiPost } from '../../api/client'
import GROUPS from '../../lib/featureGroups'
import './ScenarioTab.css'

// Features suitable for pattern criteria (exclude raw OHLCV, text columns)
const MATCHABLE_FEATURES = GROUPS.flatMap(g => g.features).filter(f =>
  !['timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta',
    'sw_ohlc', 'prev_swing_features'].includes(f)
)

const fmt = (v, feat) => {
  if (v == null || v === '') return '—'
  if (typeof v === 'number') {
    if (Number.isInteger(v) || feat?.startsWith('is_') || feat?.startsWith('bos_') ||
        feat === 'bull_div' || feat === 'bear_div' ||
        ['choch', 'macd_peak', 'macd_trough', 'swing_had_break'].includes(feat))
      return String(v)
    return v.toFixed(4)
  }
  return String(v)
}

const fmtTs = ts => {
  if (!ts) return '—'
  const d = new Date(ts)
  return d.toISOString().replace('T', ' ').slice(0, 19)
}

export default function ScenarioTab({ tf }) {
  // Meta candle selection
  const [metaTs, setMetaTs] = useState('')
  const [lookback, setLookback] = useState(3)
  const [patternData, setPatternData] = useState(null)
  const [loading, setLoading] = useState(false)

  // Criteria: {[offset]: {feat: {value, tolerance, enabled}}}
  const [criteria, setCriteria] = useState({})
  const [selectedPos, setSelectedPos] = useState(null)

  // Results
  const [results, setResults] = useState(null)
  const [matching, setMatching] = useState(false)
  const [includeWhales, setIncludeWhales] = useState(false)
  const [forwardCandles, setForwardCandles] = useState(20)
  const [matchLimit, setMatchLimit] = useState(100)

  // Expanded match detail
  const [expandedMatch, setExpandedMatch] = useState(null)

  // Message
  const [msg, setMsg] = useState('')

  // Load meta candle + lookback
  const loadPattern = useCallback(async () => {
    if (!metaTs) return
    setLoading(true)
    setMsg('')
    const ts = parseInt(metaTs, 10)
    if (isNaN(ts)) { setMsg('Invalid timestamp'); setLoading(false); return }
    const data = await api(`/api/pattern/candles?ts=${ts}&tf=${tf}&lookback=${lookback}&forward=5`)
    setLoading(false)
    if (!data || data.error) {
      setMsg(data?.error || 'Candle not found')
      setPatternData(null)
      return
    }
    setPatternData(data)
    setResults(null)
    // Init criteria from loaded candle positions
    const newCriteria = {}
    newCriteria[0] = {}
    for (let i = 0; i < data.before.length; i++) {
      newCriteria[-(data.before.length - i)] = {}
    }
    setCriteria(newCriteria)
    setSelectedPos(0)
  }, [metaTs, tf, lookback])

  // Load latest candle as default meta
  useEffect(() => {
    if (!metaTs) {
      api(`/api/db/candles?tf=${tf}&limit=1&order=desc`).then(data => {
        if (data?.candles?.[0]) {
          setMetaTs(String(data.candles[0].timestamp))
        }
      })
    }
  }, [tf])

  // Run pattern match
  const runMatch = async () => {
    if (!patternData) { setMsg('Load a candle first'); return }

    // Build criteria array from state
    const criteriaArr = []
    for (const [offset, feats] of Object.entries(criteria)) {
      const enabledFeats = {}
      for (const [feat, spec] of Object.entries(feats)) {
        if (spec.enabled) {
          enabledFeats[feat] = { value: spec.value, tolerance: spec.tolerance }
        }
      }
      if (Object.keys(enabledFeats).length > 0) {
        criteriaArr.push({ offset: parseInt(offset, 10), features: enabledFeats })
      }
    }

    if (criteriaArr.length === 0) {
      setMsg('Set at least one feature criterion')
      return
    }

    setMatching(true)
    setMsg('')
    const res = await apiPost('/api/pattern/match', {
      criteria: criteriaArr,
      tf,
      limit: matchLimit,
      forward_candles: forwardCandles,
      exclude_ts: parseInt(metaTs, 10),
      include_whales: includeWhales,
    })
    setMatching(false)
    if (!res || res.error) {
      setMsg(res?.error || 'Match failed')
      return
    }
    setResults(res)
    setExpandedMatch(null)
  }

  // Toggle a feature criterion for a position
  const toggleFeature = (offset, feat) => {
    setCriteria(prev => {
      const pos = { ...prev[offset] }
      if (pos[feat]) {
        delete pos[feat]
      } else {
        const candle = getCandleAtOffset(offset)
        const val = candle?.[feat]
        pos[feat] = {
          enabled: true,
          value: typeof val === 'number' ? val : (val || 0),
          tolerance: typeof val === 'number' && !Number.isInteger(val) ? Math.abs(val * 0.1) : 0,
        }
      }
      return { ...prev, [offset]: pos }
    })
  }

  const updateCriterion = (offset, feat, field, val) => {
    setCriteria(prev => {
      const pos = { ...prev[offset] }
      pos[feat] = { ...pos[feat], [field]: val }
      return { ...prev, [offset]: pos }
    })
  }

  const getCandleAtOffset = (offset) => {
    if (!patternData) return null
    if (offset === 0) return patternData.meta
    const idx = patternData.before.length + offset
    return patternData.before[idx] ?? null
  }

  // All positions in the pattern
  const positions = useMemo(() => {
    if (!patternData) return []
    const pos = []
    for (let i = 0; i < patternData.before.length; i++) {
      pos.push(-(patternData.before.length - i))
    }
    pos.push(0)
    return pos
  }, [patternData])

  const activeCriteriaCount = useMemo(() => {
    let count = 0
    for (const feats of Object.values(criteria)) {
      count += Object.values(feats).filter(s => s.enabled).length
    }
    return count
  }, [criteria])

  return (
    <div className="scenario-tab panel">
      {msg && <div className="scen-msg">{msg}</div>}

      {/* Top: Meta candle selector */}
      <div className="pm-header">
        <h2>PATTERN MATCHER</h2>
        <div className="pm-controls">
          <label className="pm-label">
            <span>Meta Candle (ts)</span>
            <input type="text" value={metaTs} onChange={e => setMetaTs(e.target.value)}
              placeholder="Timestamp..." className="pm-input pm-input--wide" />
          </label>
          <label className="pm-label">
            <span>Lookback</span>
            <input type="number" min={1} max={20} value={lookback}
              onChange={e => setLookback(Math.max(1, Math.min(20, +e.target.value)))}
              className="pm-input" />
          </label>
          <button className="stab-btn stab-btn--primary" onClick={loadPattern}
            disabled={loading}>
            {loading ? 'Loading...' : 'Load'}
          </button>
          <label className="pm-label">
            <span>Forward</span>
            <input type="number" min={5} max={60} value={forwardCandles}
              onChange={e => setForwardCandles(+e.target.value)} className="pm-input" />
          </label>
          <label className="pm-label">
            <span>Max Matches</span>
            <input type="number" min={10} max={500} value={matchLimit}
              onChange={e => setMatchLimit(+e.target.value)} className="pm-input" />
          </label>
          <label className="pm-check">
            <input type="checkbox" checked={includeWhales}
              onChange={e => setIncludeWhales(e.target.checked)} />
            <span>Whales</span>
          </label>
          <button className="stab-btn stab-btn--accent" onClick={runMatch}
            disabled={matching || !patternData || activeCriteriaCount === 0}>
            {matching ? 'Matching...' : `Match (${activeCriteriaCount} criteria)`}
          </button>
        </div>
      </div>

      {patternData && (
        <div className="pm-body">
          {/* Pattern sequence bar */}
          <div className="pm-sequence">
            {positions.map(offset => {
              const candle = getCandleAtOffset(offset)
              const isSelected = selectedPos === offset
              const criteriaCount = Object.values(criteria[offset] || {}).filter(s => s.enabled).length
              const isMeta = offset === 0
              return (
                <div key={offset}
                  className={`pm-candle-slot ${isSelected ? 'selected' : ''} ${isMeta ? 'meta' : ''}`}
                  onClick={() => setSelectedPos(offset)}>
                  <div className="pm-slot-label">{isMeta ? 'META' : `${offset}`}</div>
                  <div className={`pm-slot-bar ${candle?.is_bullish ? 'bull' : 'bear'}`}>
                    <span className="pm-slot-price">{candle?.close?.toFixed(0)}</span>
                  </div>
                  <div className="pm-slot-ts">{fmtTs(candle?.timestamp).slice(11, 19)}</div>
                  {criteriaCount > 0 && (
                    <div className="pm-slot-badge">{criteriaCount}</div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Criteria editor for selected position */}
          {selectedPos !== null && (
            <div className="pm-criteria">
              <h3>
                CRITERIA — Position {selectedPos === 0 ? 'META (0)' : selectedPos}
                <span className="pm-criteria-sub">
                  {fmtTs(getCandleAtOffset(selectedPos)?.timestamp)}
                </span>
              </h3>
              <div className="pm-criteria-grid">
                {GROUPS.map(group => {
                  const relevantFeats = group.features.filter(f => MATCHABLE_FEATURES.includes(f))
                  if (relevantFeats.length === 0) return null
                  const candle = getCandleAtOffset(selectedPos)
                  return (
                    <div key={group.id} className="pm-feat-group">
                      <div className="pm-group-header" style={{ borderColor: group.color }}>
                        {group.name}
                      </div>
                      {relevantFeats.map(feat => {
                        const val = candle?.[feat]
                        const spec = criteria[selectedPos]?.[feat]
                        const isActive = spec?.enabled
                        return (
                          <div key={feat} className={`pm-feat-row ${isActive ? 'active' : ''}`}>
                            <label className="pm-feat-toggle">
                              <input type="checkbox" checked={!!isActive}
                                onChange={() => toggleFeature(selectedPos, feat)} />
                              <span className="pm-feat-name">{feat}</span>
                            </label>
                            <span className="pm-feat-val" style={{ color: group.color }}>
                              {fmt(val, feat)}
                            </span>
                            {isActive && (
                              <div className="pm-feat-tol">
                                <input type="number" step="any" value={spec.value}
                                  onChange={e => updateCriterion(selectedPos, feat, 'value', +e.target.value)}
                                  className="pm-tol-input" />
                                <span className="pm-tol-pm">&plusmn;</span>
                                <input type="number" step="any" min={0} value={spec.tolerance}
                                  onChange={e => updateCriterion(selectedPos, feat, 'tolerance', Math.max(0, +e.target.value))}
                                  className="pm-tol-input" />
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Results */}
      {results && (
        <div className="pm-results">
          <div className="pm-stats-bar">
            <h3>RESULTS</h3>
            <div className="pm-stats-grid">
              <div className="pm-stat">
                <span className="pm-stat-label">Matches</span>
                <span className="pm-stat-value">{results.stats.total}</span>
              </div>
              {results.stats.long_pct != null && (
                <>
                  <div className="pm-stat">
                    <span className="pm-stat-label">Long %</span>
                    <span className="pm-stat-value up">{results.stats.long_pct}%</span>
                  </div>
                  <div className="pm-stat">
                    <span className="pm-stat-label">Short %</span>
                    <span className="pm-stat-value down">{results.stats.short_pct}%</span>
                  </div>
                </>
              )}
              {results.stats.avg_max_favorable != null && (
                <>
                  <div className="pm-stat">
                    <span className="pm-stat-label">Avg Max Fav</span>
                    <span className="pm-stat-value up">{results.stats.avg_max_favorable}%</span>
                  </div>
                  <div className="pm-stat">
                    <span className="pm-stat-label">Avg Max Adv</span>
                    <span className="pm-stat-value down">{results.stats.avg_max_adverse}%</span>
                  </div>
                </>
              )}
              {['pnl_5c', 'pnl_10c', 'pnl_20c'].map(key => results.stats[key] && (
                <div className="pm-stat" key={key}>
                  <span className="pm-stat-label">{key.replace('pnl_', 'PnL ')}</span>
                  <span className={`pm-stat-value ${results.stats[key].avg >= 0 ? 'up' : 'down'}`}>
                    {results.stats[key].avg}% (WR: {results.stats[key].win_rate}%)
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Match list */}
          <div className="pm-match-list">
            <table className="pm-match-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Timestamp</th>
                  <th>Price</th>
                  <th>Dir</th>
                  <th>PnL 5c</th>
                  <th>PnL 10c</th>
                  <th>PnL 20c</th>
                  <th>Max Fav</th>
                  <th>Max Adv</th>
                  {includeWhales && <th>Whales</th>}
                </tr>
              </thead>
              <tbody>
                {results.matches.map((m, i) => {
                  const o = m.outcome
                  const expanded = expandedMatch === i
                  return [
                    <tr key={i} className={`pm-match-row ${expanded ? 'expanded' : ''}`}
                      onClick={() => setExpandedMatch(expanded ? null : i)}>
                      <td>{i + 1}</td>
                      <td className="pm-ts-cell">{fmtTs(m.meta.timestamp)}</td>
                      <td>{m.meta.close?.toFixed(1)}</td>
                      <td className={o.direction === 'LONG' ? 'up' : 'down'}>
                        {o.direction || '—'}
                      </td>
                      <td className={(o.pnl_5c ?? 0) >= 0 ? 'up' : 'down'}>
                        {o.pnl_5c?.toFixed(3) ?? '—'}%
                      </td>
                      <td className={(o.pnl_10c ?? 0) >= 0 ? 'up' : 'down'}>
                        {o.pnl_10c?.toFixed(3) ?? '—'}%
                      </td>
                      <td className={(o.pnl_20c ?? 0) >= 0 ? 'up' : 'down'}>
                        {o.pnl_20c?.toFixed(3) ?? '—'}%
                      </td>
                      <td className="up">{o.max_favorable?.toFixed(3) ?? '—'}%</td>
                      <td className="down">{o.max_adverse?.toFixed(3) ?? '—'}%</td>
                      {includeWhales && (
                        <td>
                          {m.whale_context?.summary ? (
                            <span className={`pm-whale-tag ${m.whale_context.summary.net_sentiment.toLowerCase()}`}>
                              {m.whale_context.summary.net_sentiment}
                            </span>
                          ) : '—'}
                        </td>
                      )}
                    </tr>,
                    expanded && (
                      <tr key={`${i}-detail`} className="pm-match-detail-row">
                        <td colSpan={includeWhales ? 10 : 9}>
                          <MatchDetail match={m} includeWhales={includeWhales} />
                        </td>
                      </tr>
                    ),
                  ]
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function MatchDetail({ match, includeWhales }) {
  const m = match
  return (
    <div className="pm-detail">
      <div className="pm-detail-cols">
        <div className="pm-detail-section">
          <h4>META CANDLE FEATURES</h4>
          <div className="pm-detail-feats">
            {GROUPS.map(g => {
              const feats = g.features.filter(f =>
                !['timestamp', 'sw_ohlc', 'prev_swing_features'].includes(f))
              return feats.map(f => (
                <span key={f} className="pm-detail-feat" style={{ color: g.color }}>
                  {f}: {fmt(m.meta[f], f)}
                </span>
              ))
            })}
          </div>
        </div>

        {includeWhales && m.whale_context?.summary && (
          <div className="pm-detail-section">
            <h4>WHALE CONTEXT</h4>
            <div className="pm-detail-whale">
              <span className="up">Buys: {m.whale_context.summary.whale_buys} (${(m.whale_context.summary.buy_volume_usd / 1000).toFixed(0)}k)</span>
              <span className="down">Sells: {m.whale_context.summary.whale_sells} (${(m.whale_context.summary.sell_volume_usd / 1000).toFixed(0)}k)</span>
              <span>Liq Long: {m.whale_context.summary.liquidations_long}</span>
              <span>Liq Short: {m.whale_context.summary.liquidations_short}</span>
              <span>Total Liq: ${(m.whale_context.summary.total_liq_usd / 1000).toFixed(0)}k</span>
            </div>
          </div>
        )}
      </div>

      {m.before?.length > 0 && (
        <div className="pm-detail-section">
          <h4>LOOKBACK CANDLES</h4>
          <div className="pm-detail-lookback">
            {m.before.map((c, i) => (
              <span key={i} className="pm-detail-lb">
                [{-(m.before.length - i)}] {fmtTs(c.timestamp).slice(11, 19)} {c.close?.toFixed(1)}
                {c.is_bullish ? ' ▲' : ' ▼'}
                {' '}RSI:{c.rsi14?.toFixed(0)} VOL:{c.vol_vs_ma?.toFixed(1)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
