import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { api, apiPost } from '../../api/client'
import GROUPS, { FEATURE_META, TIER1_FEATURES, TIER2_FEATURES, TIER3_FEATURES, featureToGroup, CONTEXT_LABELS } from '../../lib/featureGroups'
import OutcomeOverlay from './OutcomeOverlay'
import ChatPanel from './ChatPanel'
import './ScenarioTab.css'

// Features suitable for pattern criteria (exclude raw OHLCV, text columns)
const MATCHABLE_FEATURES = GROUPS.flatMap(g => g.features).filter(f =>
  !['timestamp', 'open', 'high', 'low', 'close', 'volume', 'delta',
    'sw_ohlc', 'prev_swing_features'].includes(f)
)

// Binary features that should default to bool operator
const BINARY_FEATURES = new Set([
  'is_bullish', 'is_swing_high', 'is_swing_low', 'bos_bull', 'bos_bear',
  'choch', 'bos_body', 'bos_wick', 'sw_bullish', 'same_dir',
  'broken_was_seeker', 'broken_was_seeker_div', 'swing_had_break',
  'macd_peak', 'macd_trough', 'bull_div', 'bear_div', 'div_near_daily',
  'is_seeker_hs', 'is_seeker_ls', 'is_seeker_div', 'is_seeker_kill',
  'candle_was_seeker', 'candle_was_seeker_div', 'htf_bos',
  'whale_cluster', 'elite_whale_active',
])

const OPERATOR_OPTIONS = [
  { value: '=', label: '= ± tol' },
  { value: 'bool', label: 'bool' },
  { value: '>', label: '>' },
  { value: '<', label: '<' },
  { value: '>=', label: '>=' },
  { value: '<=', label: '<=' },
  { value: 'range', label: 'range' },
]

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
  return new Date(ts).toLocaleString('sv-SE', { timeZone: 'Europe/Berlin' })
}

/* ── Individual Candle Slot ─────────────────────────────────── */
function CandleSlot({ candle, label, selected, isMeta, criteriaCount, onClick, onSaveRef }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !candle) return
    const ctx = canvas.getContext('2d')
    const dpr = window.devicePixelRatio || 1
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    canvas.width = w * dpr
    canvas.height = h * dpr
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)

    const c = candle
    const range = c.high - c.low
    if (range <= 0) return

    const isBull = c.close >= c.open
    const bodyCol = isBull ? '#00ff88' : '#ff3355'
    const accent = isMeta ? '#ffaa00' : '#00f0ff'

    const padT = 10, padB = 10
    const chartH = h - padT - padB
    const priceX = 50
    const candleX = Math.round(w * 0.47)
    const bodyW = 22
    const annotX = candleX + bodyW / 2 + 8

    const pPad = range * 0.06
    const pMin = c.low - pPad
    const pMax = c.high + pPad
    const pSpan = pMax - pMin
    const toY = (p) => padT + chartH * (1 - (p - pMin) / pSpan)

    // wick
    ctx.strokeStyle = bodyCol
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(candleX, toY(c.high))
    ctx.lineTo(candleX, toY(c.low))
    ctx.stroke()

    // body
    const bTop = toY(Math.max(c.open, c.close))
    const bBot = toY(Math.min(c.open, c.close))
    const bH = Math.max(2, bBot - bTop)
    ctx.fillStyle = isBull ? 'rgba(0,255,136,0.85)' : 'rgba(255,51,85,0.85)'
    ctx.fillRect(candleX - bodyW / 2, bTop, bodyW, bH)
    ctx.strokeStyle = bodyCol
    ctx.lineWidth = 1.5
    ctx.strokeRect(candleX - bodyW / 2, bTop, bodyW, bH)

    // measurements
    const upperWick = c.high - Math.max(c.open, c.close)
    const lowerWick = Math.min(c.open, c.close) - c.low
    const bodySize = Math.abs(c.close - c.open)
    const upperPct = (upperWick / range * 100)
    const bodyPct = (bodySize / range * 100)
    const lowerPct = (lowerWick / range * 100)

    const drawBracket = (x, y1, y2, color, text) => {
      if (y2 - y1 < 16) return
      const mid = (y1 + y2) / 2
      ctx.strokeStyle = color
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x - 3, y1 + 1); ctx.lineTo(x + 3, y1 + 1)
      ctx.moveTo(x, y1 + 1); ctx.lineTo(x, y2 - 1)
      ctx.moveTo(x - 3, y2 - 1); ctx.lineTo(x + 3, y2 - 1)
      ctx.stroke()
      ctx.fillStyle = color
      ctx.font = '10px monospace'
      ctx.textAlign = 'left'
      ctx.fillText(text, x + 6, mid + 4)
    }

    drawBracket(annotX, toY(c.high), bTop, 'rgba(0,240,255,0.7)', `${upperPct.toFixed(1)}%`)
    drawBracket(annotX, bTop, bTop + bH, bodyCol, `${bodyPct.toFixed(1)}%`)
    drawBracket(annotX, bTop + bH, toY(c.low), 'rgba(0,240,255,0.7)', `${lowerPct.toFixed(1)}%`)

    // OHLC prices (left) — always show all 4 values
    ctx.font = '9px monospace'
    ctx.textAlign = 'right'
    const oY = toY(c.open), cY = toY(c.close)
    const hY = toY(c.high), lY = toY(c.low)

    // Collect all labels, then deduplicate positions
    const labels = [
      { text: `H ${c.high.toFixed(0)}`, y: hY, color: accent },
      { text: `O ${c.open.toFixed(0)}`, y: oY, color: bodyCol },
      { text: `C ${c.close.toFixed(0)}`, y: cY, color: bodyCol },
      { text: `L ${c.low.toFixed(0)}`, y: lY, color: accent },
    ]
    // Push apart overlapping labels (min 11px gap)
    labels.sort((a, b) => a.y - b.y)
    for (let i = 1; i < labels.length; i++) {
      if (labels[i].y - labels[i - 1].y < 11) {
        labels[i].y = labels[i - 1].y + 11
      }
    }
    for (const lb of labels) {
      ctx.fillStyle = lb.color
      ctx.fillText(lb.text, priceX, lb.y + 3)
    }
  }, [candle, selected, isMeta])

  if (!candle) return null

  const c = candle
  const isBull = c.close >= c.open

  // Build info lines from candle analyzer data
  const tags = []

  // Structure events
  if (c.bos_bull) tags.push({ l: 'BOS BULL', c: '#00ff88' })
  if (c.bos_bear) tags.push({ l: 'BOS BEAR', c: '#ff3355' })
  if (c.choch) tags.push({ l: 'CHoCH', c: '#ffd700' })
  // SW HIGH/LOW shown as prominent banner below, not as small tag

  // Divergences
  if (c.bull_div) tags.push({ l: `DIV+ ${(c.div_strength || 0).toFixed(2)}`, c: '#44ffaa' })
  if (c.bear_div) tags.push({ l: `DIV- ${(c.div_strength || 0).toFixed(2)}`, c: '#aa44ff' })

  // Seeker
  if (c.is_seeker_div) {
    tags.push({ l: `SKR DIV #${c.seeker_div_nr || '?'}`, c: '#00e5ff' })
  }
  if (c.is_seeker_kill) {
    const kts = c.killed_seeker_ts
    const ktime = kts ? `@${String(new Date(kts).getHours()).padStart(2,'0')}:${String(new Date(kts).getMinutes()).padStart(2,'0')} ` : ''
    tags.push({ l: `SKR KILL ${ktime}×${c.killed_seeker_divs || 0}`, c: '#ff00ff' })
  }

  // Break quality (only if BOS active)
  if ((c.bos_bull || c.bos_bear) && c.break_depth) {
    tags.push({ l: `Depth ${c.break_depth.toFixed(2)}`, c: '#c0a0ff' })
    if (c.bos_body) tags.push({ l: 'BODY-BRK', c: '#e8c040' })
    // swing_age now shown inside break banner
  }

  const vol = c.vol_vs_ma || 0
  const volBtc = c.volume || 0
  const deltaPct = (c.delta_pct || 0) * 100
  const bodyPos = c.body_position
  const bodyPosText = bodyPos >= 0.85 ? 'Close oben' :
    bodyPos >= 0.65 ? 'Close ob. mitte' :
    bodyPos >= 0.35 ? 'Close mitte' :
    bodyPos >= 0.15 ? 'Close unt. mitte' : 'Close unten'

  const fmtSwingTs = (ts) => {
    if (!ts) return ''
    const d = new Date(ts)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }

  const ws = c.whale_sentiment || 0

  return (
    <div className={`pm-slot ${selected ? 'selected' : ''} ${isMeta ? 'meta' : ''}`}
      onClick={onClick}>
      <div className="pm-slot-header">
        <span className="pm-slot-label">{label}</span>
        {criteriaCount > 0 && <span className="pm-slot-badge">{criteriaCount}</span>}
        <span className={`pm-slot-dir ${isBull ? 'up' : 'down'}`}>{isBull ? '▲' : '▼'}</span>
      </div>
      <canvas ref={canvasRef} className="pm-slot-canvas" />
      <div className="pm-slot-info">
        {tags.length > 0 && (
          <div className="pm-slot-tags">
            {tags.map((t, i) => <span key={i} className={`pm-slot-tag${t.bg ? ' pm-slot-tag-filled' : ''}`} style={{ color: t.bg ? t.c : t.c, borderColor: t.bg || t.c, background: t.bg || 'transparent' }}>{t.l}</span>)}
          </div>
        )}

        {/* Swing Status — prominent banner */}
        {(c.is_swing_high || c.is_swing_low) && (
          <div className="pm-slot-swing-banner">
            {c.is_swing_high ? <span className="pm-swing-badge high">⬆ SWING HIGH</span> : null}
            {c.is_swing_low ? <span className="pm-swing-badge low">⬇ SWING LOW</span> : null}
          </div>
        )}

        {/* Break Info — prominent banner */}
        {(c.bos_bull || c.bos_bear) && (
          <div className="pm-slot-break-banner">
            {c.bos_bull ? (
              <span className="pm-break-badge bull">
                ⚡ BRICHT SW HIGH{(c.breaks_highs || 0) > 1 ? ` ×${c.breaks_highs}` : ''}
                {c.broken_swing_ts ? <small> @{fmtSwingTs(c.broken_swing_ts)}</small> : null}
                {c.swing_age ? <small> ({c.swing_age}K entfernt)</small> : null}
              </span>
            ) : null}
            {c.bos_bear ? (
              <span className="pm-break-badge bear">
                ⚡ BRICHT SW LOW{(c.breaks_lows || 0) > 1 ? ` ×${c.breaks_lows}` : ''}
                {c.broken_swing_ts ? <small> @{fmtSwingTs(c.broken_swing_ts)}</small> : null}
                {c.swing_age ? <small> ({c.swing_age}K entfernt)</small> : null}
              </span>
            ) : null}
          </div>
        )}

        {/* Volume Heat Bar */}
        {(() => {
          const volClamped = Math.min(vol, 4)
          const pct = Math.min((volClamped / 4) * 100, 100)
          const volColor = vol >= 2.5 ? '#ff3355' : vol >= 1.5 ? '#00ff88' : '#3a6080'
          const bgColor = vol >= 2.5 ? 'rgba(255,51,85,0.12)' : vol >= 1.5 ? 'rgba(0,255,136,0.08)' : 'rgba(58,96,128,0.08)'
          const btcLabel = volBtc >= 100 ? `${(volBtc).toFixed(0)} BTC` : `${volBtc.toFixed(1)} BTC`
          return (
            <div className="pm-vol-bar-wrap">
              <div className="pm-vol-bar-track" style={{ background: bgColor }}>
                <div className="pm-vol-bar-fill" style={{ width: `${pct}%`, background: volColor }} />
                <span className="pm-vol-bar-label" style={{ color: vol >= 1.5 ? '#fff' : '#8899aa' }}>
                  {btcLabel} · {vol.toFixed(1)}x{vol >= 1.5 ? ' SPIKE' : ''}
                </span>
              </div>
              <div className="pm-vol-bar-side">
                <span style={{ color: deltaPct > 30 ? '#00ff88' : deltaPct < -30 ? '#ff3355' : '#667788' }}>
                  D{deltaPct > 0 ? '+' : ''}{deltaPct.toFixed(0)}%
                </span>
              </div>
            </div>
          )
        })()}

        {/* Body Position */}
        <div className="pm-slot-metrics">
          <span>{bodyPosText}</span>
        </div>

        {/* Whale */}
        {ws !== 0 && (
          <div className="pm-slot-metrics">
            <span style={{ color: ws > 0 ? '#00ff88' : '#ff3355' }}>
              Whale {ws > 0 ? 'BULL' : 'BEAR'}
            </span>
          </div>
        )}
      </div>
      <div className="pm-slot-footer">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="pm-slot-ts">{fmtTs(c.timestamp)?.slice(11, 16)}</span>
          {onSaveRef && (
            <button className="pm-ref-save-btn" onClick={e => { e.stopPropagation(); onSaveRef(c.timestamp) }}
              title="Save as Reference">REF</button>
          )}
        </div>
      </div>
    </div>
  )
}

// Session storage helpers
const SS_KEY = 'edge_scenario'
function loadSession() {
  try {
    const raw = sessionStorage.getItem(SS_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}
function saveSession(data) {
  try { sessionStorage.setItem(SS_KEY, JSON.stringify(data)) } catch {}
}

export default function ScenarioTab({ tf, initialTs, onTsClear, signals }) {
  // Restore from session storage
  const _saved = useRef(loadSession())

  // Meta candle selection
  const [metaTs, setMetaTs] = useState(() => _saved.current?.metaTs || '')
  const [lookback, setLookback] = useState(() => _saved.current?.lookback || 3)
  const [patternData, setPatternData] = useState(null)
  const [loading, setLoading] = useState(false)

  // Criteria: {[offset]: {feat: {value, tolerance, enabled}}}
  const [criteria, setCriteria] = useState(() => _saved.current?.criteria || {})
  const [selectedPos, setSelectedPos] = useState(() => _saved.current?.selectedPos ?? null)

  // Results
  const [results, setResults] = useState(null)
  const [matching, setMatching] = useState(false)
  const [includeWhales, setIncludeWhales] = useState(false)
  const [forwardCandles, setForwardCandles] = useState(() => _saved.current?.forwardCandles || 20)
  const [matchLimit, setMatchLimit] = useState(() => _saved.current?.matchLimit || 100)

  // Persist key state to sessionStorage
  useEffect(() => {
    saveSession({ metaTs, lookback, criteria, selectedPos, forwardCandles, matchLimit })
  }, [metaTs, lookback, criteria, selectedPos, forwardCandles, matchLimit])

  // Auto-reload pattern data on mount if we have a saved metaTs
  useEffect(() => {
    if (_saved.current?.metaTs && !initialTs && !patternData) {
      const ts = parseInt(_saved.current.metaTs, 10)
      if (!isNaN(ts)) {
        setLoading(true)
        api(`/api/pattern/candles?ts=${ts}&tf=${tf}&lookback=${_saved.current.lookback || 3}&forward=5`).then(data => {
          setLoading(false)
          if (data && !data.error) {
            setPatternData(data)
            setSelectedPos(prev => prev ?? 0)
          }
        })
      }
    }
  }, [])

  // Expanded match detail
  const [expandedMatch, setExpandedMatch] = useState(null)

  // Pattern save/load dialogs
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [showLoadDialog, setShowLoadDialog] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [saveDesc, setSaveDesc] = useState('')
  const [savedPatterns, setSavedPatterns] = useState([])
  const [loadingPatterns, setLoadingPatterns] = useState(false)

  // Discovery
  const [showDiscovery, setShowDiscovery] = useState(false)
  const [discoveries, setDiscoveries] = useState([])
  const [discovering, setDiscovering] = useState(false)

  // Reference Library
  const [showRefPanel, setShowRefPanel] = useState(false)
  const [references, setReferences] = useState([])
  const [refCount, setRefCount] = useState(0)
  const [loadingRefs, setLoadingRefs] = useState(false)
  const [scanningRef, setScanningRef] = useState(null)

  // Dismiss signal
  const dismissSignal = async (id) => {
    await apiPost('/api/signals/dismiss', { id })
  }

  // Load reference count on mount
  useEffect(() => {
    api(`/api/references?tf=${tf}&limit=1`).then(res => {
      if (res?.references) setRefCount(res.references.length)
    })
    // Full count
    api('/api/references?limit=500').then(res => {
      if (res?.references) setRefCount(res.references.length)
    })
  }, [tf])

  // Save a candle as reference
  const saveReference = async (ts) => {
    setMsg('')
    const res = await apiPost('/api/reference/save', {
      ts, tf, forward_candles: forwardCandles,
    })
    if (res?.ok) {
      setMsg(`Referenz gespeichert — ${res.features_extracted} Features`)
      setRefCount(prev => prev + 1)
      // Refresh refs if panel is open
      if (showRefPanel) loadReferences()
    } else {
      setMsg(res?.error || 'Referenz speichern fehlgeschlagen')
    }
  }

  // Load references list
  const loadReferences = async () => {
    setLoadingRefs(true)
    const res = await api('/api/references?limit=100')
    setLoadingRefs(false)
    if (res?.references) {
      setReferences(res.references)
      setRefCount(res.references.length)
    }
  }

  // Toggle reference panel
  const toggleRefPanel = () => {
    const next = !showRefPanel
    setShowRefPanel(next)
    if (next) loadReferences()
  }

  // Click on a reference → load as meta candle + fill criteria
  const loadReference = async (ref) => {
    if (!ref.meta_ts) return
    setMetaTs(String(ref.meta_ts))
    setLoading(true)
    const data = await api(`/api/pattern/candles?ts=${ref.meta_ts}&tf=${ref.tf || tf}&lookback=${lookback}&forward=5`)
    setLoading(false)
    if (data && !data.error) {
      setPatternData(data)
      setResults(null)
      // Load criteria from reference
      const refDetail = await api(`/api/pattern/load/${ref.id}`)
      if (refDetail && refDetail.criteria) {
        const newCriteria = {}
        const patCount = Math.min(lookback, data.before.length)
        newCriteria[0] = {}
        for (let i = 0; i < patCount; i++) {
          newCriteria[-(patCount - i)] = {}
        }
        for (const c of refDetail.criteria) {
          const offset = c.offset
          if (newCriteria[offset] === undefined) newCriteria[offset] = {}
          for (const [feat, spec] of Object.entries(c.features || {})) {
            newCriteria[offset][feat] = { enabled: true, ...spec }
          }
        }
        setCriteria(newCriteria)
        setSelectedPos(0)
        setMsg(`Referenz "${ref.name}" geladen`)
      }
    }
  }

  // Scan with a reference's criteria
  const scanReference = async (ref) => {
    setScanningRef(ref.id)
    const res = await apiPost('/api/reference/scan', {
      ref_id: ref.id, limit: matchLimit,
    })
    setScanningRef(null)
    if (res?.matches) {
      setResults(res)
      setExpandedMatch(null)
      setMsg(`Scan: ${res.stats?.total || 0} Matches gefunden`)
    } else {
      setMsg(res?.error || 'Scan fehlgeschlagen')
    }
  }

  // Run auto-discovery
  const runDiscovery = async () => {
    setDiscovering(true)
    setMsg('')
    const res = await apiPost('/api/pattern/discover', {
      tf,
      lookback,
      forward_candles: forwardCandles,
      min_cluster_size: 5,
      min_win_rate: 55,
      scan_limit: 10000,
    })
    setDiscovering(false)
    if (res?.discoveries) {
      setDiscoveries(res.discoveries)
      if (res.discoveries.length === 0) setMsg('No patterns found with these thresholds')
    } else {
      setMsg(res?.error || 'Discovery failed')
    }
  }

  // Use a discovery as criteria
  const useDiscovery = (disc) => {
    // Build criteria from suggested_criteria
    const newCriteria = { 0: {} }
    for (const sc of disc.suggested_criteria || []) {
      newCriteria[0][sc.feature] = {
        enabled: true,
        op: sc.op,
        value: sc.value ?? 0,
        tolerance: sc.tolerance ?? 0,
        min: sc.min,
        max: sc.max,
      }
    }
    setCriteria(newCriteria)
    setSelectedPos(0)
    setShowDiscovery(false)
    setMsg(`Discovery loaded with ${disc.suggested_criteria?.length || 0} criteria`)
  }

  // Save discovery directly as pattern
  const saveDiscovery = async (disc) => {
    const name = `auto_${disc.direction}_WR${disc.win_rate}_${Date.now().toString(36)}`
    const criteriaArr = [{
      offset: 0,
      features: Object.fromEntries(
        (disc.suggested_criteria || []).map(sc => [sc.feature, {
          op: sc.op, value: sc.value, tolerance: sc.tolerance,
          min: sc.min, max: sc.max,
        }])
      )
    }]
    const res = await apiPost('/api/pattern/save', {
      name,
      description: `Auto-discovered: ${disc.direction} WR:${disc.win_rate}% Size:${disc.size}`,
      tf,
      lookback: 0,
      meta_ts: 0,
      criteria: criteriaArr,
      is_active: 1,
    })
    if (res?.ok) setMsg(`Pattern "${name}" saved`)
    else setMsg(res?.error || 'Save failed')
  }

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
    // Init criteria — only for actual lookback positions (not extra context)
    const patternCount = Math.min(lookback, data.before.length)
    const newCriteria = {}
    newCriteria[0] = {}
    for (let i = 0; i < patternCount; i++) {
      newCriteria[-(patternCount - i)] = {}
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

  // Auto-load when coming from Feature Browser via "Use as Pattern" button
  useEffect(() => {
    if (initialTs) {
      setMetaTs(String(initialTs))
      // Trigger load after state update
      const ts = initialTs
      setTimeout(() => {
        api(`/api/pattern/candles?ts=${ts}&tf=${tf}&lookback=${lookback}&forward=5`).then(data => {
          if (data && !data.error) {
            setPatternData(data)
            setResults(null)
            const patternCount = Math.min(lookback, data.before.length)
            const newCriteria = {}
            newCriteria[0] = {}
            for (let i = 0; i < patternCount; i++) {
              newCriteria[-(patternCount - i)] = {}
            }
            setCriteria(newCriteria)
            setSelectedPos(0)
          }
        })
      }, 50)
      if (onTsClear) onTsClear()
    }
  }, [initialTs])

  // Build criteria array from current state — shared helper
  const buildCriteriaArray = useCallback(() => {
    const criteriaArr = []
    for (const [offset, feats] of Object.entries(criteria)) {
      const enabledFeats = {}
      for (const [feat, spec] of Object.entries(feats)) {
        if (!spec.enabled) continue
        const entry = { value: spec.value, op: spec.op || '=' }
        if (entry.op === '=') {
          entry.tolerance = spec.tolerance || 0
        } else if (entry.op === 'range') {
          entry.min = spec.min ?? 0
          entry.max = spec.max ?? 0
          delete entry.value
        } else if (entry.op === 'bool') {
          entry.value = spec.value ? 1 : 0
        }
        enabledFeats[feat] = entry
      }
      if (Object.keys(enabledFeats).length > 0) {
        criteriaArr.push({ offset: parseInt(offset, 10), features: enabledFeats })
      }
    }
    return criteriaArr
  }, [criteria])

  // Save current pattern
  const saveCurrentPattern = async () => {
    if (!saveName.trim()) { setMsg('Pattern name required'); return }
    const criteriaArr = buildCriteriaArray()
    const res = await apiPost('/api/pattern/save', {
      name: saveName.trim(),
      description: saveDesc,
      tf,
      lookback,
      meta_ts: parseInt(metaTs, 10) || 0,
      criteria: criteriaArr,
      is_active: 1,
    })
    if (res?.ok) {
      setMsg(`Pattern "${saveName}" saved`)
      setShowSaveDialog(false)
      setSaveName('')
      setSaveDesc('')
    } else {
      setMsg(res?.error || 'Save failed')
    }
  }

  // Load patterns list
  const openLoadDialog = async () => {
    setShowLoadDialog(true)
    setLoadingPatterns(true)
    const res = await api('/api/patterns')
    setLoadingPatterns(false)
    if (res?.patterns) setSavedPatterns(res.patterns)
  }

  // Load a saved pattern into editor
  const loadSavedPattern = async (pat) => {
    setShowLoadDialog(false)
    setMetaTs(String(pat.meta_ts || ''))
    setLookback(pat.lookback || 3)

    // Load candle data
    if (pat.meta_ts) {
      setLoading(true)
      const data = await api(`/api/pattern/candles?ts=${pat.meta_ts}&tf=${pat.tf || tf}&lookback=${pat.lookback || 3}&forward=5`)
      setLoading(false)
      if (data && !data.error) {
        setPatternData(data)
        setResults(null)

        // Restore criteria from saved pattern
        const newCriteria = {}
        const patternCount = Math.min(pat.lookback || 3, data.before.length)
        newCriteria[0] = {}
        for (let i = 0; i < patternCount; i++) {
          newCriteria[-(patternCount - i)] = {}
        }
        // Apply saved criteria
        for (const c of (pat.criteria || [])) {
          const offset = c.offset
          if (newCriteria[offset] === undefined) newCriteria[offset] = {}
          for (const [feat, spec] of Object.entries(c.features || {})) {
            newCriteria[offset][feat] = { enabled: true, ...spec }
          }
        }
        setCriteria(newCriteria)
        setSelectedPos(0)
        setMsg(`Pattern "${pat.name}" loaded`)
      } else {
        setMsg(data?.error || 'Failed to load candle data for pattern')
      }
    }
  }

  // Delete a saved pattern
  const deleteSavedPattern = async (pat) => {
    const res = await apiPost('/api/pattern/delete', { id: pat.id })
    if (res?.ok) {
      setSavedPatterns(prev => prev.filter(p => p.id !== pat.id))
    }
  }

  // Run pattern match
  const runMatch = async () => {
    if (!patternData) { setMsg('Load a candle first'); return }

    const criteriaArr = buildCriteriaArray()

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
      include_forward: true,
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
        if (BINARY_FEATURES.has(feat)) {
          pos[feat] = {
            enabled: true,
            op: 'bool',
            value: val ? 1 : 0,
            tolerance: 0,
          }
        } else {
          pos[feat] = {
            enabled: true,
            op: '=',
            value: typeof val === 'number' ? val : (val || 0),
            tolerance: typeof val === 'number' && !Number.isInteger(val) ? Math.abs(val * 0.1) : 0,
          }
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

  // AI Chat action handler
  const handleChatAction = (action) => {
    const pos = selectedPos ?? 0
    if (action.action === 'toggle') {
      setCriteria(prev => ({
        ...prev,
        [pos]: {
          ...prev[pos],
          [action.feature]: {
            enabled: true,
            op: action.op,
            value: action.value,
            tolerance: action.tolerance || 0,
            min: action.min,
            max: action.max,
          }
        }
      }))
    } else if (action.action === 'remove') {
      setCriteria(prev => {
        const posCrit = { ...prev[pos] }
        delete posCrit[action.feature]
        return { ...prev, [pos]: posCrit }
      })
    }
  }

  // Pattern positions are only the last `lookback` candles from before[]
  const patternCount = patternData ? Math.min(lookback, patternData.before.length) : 0

  const getCandleAtOffset = (offset) => {
    if (!patternData) return null
    if (offset === 0) return patternData.meta
    // offset is negative, e.g. -3, -2, -1
    // map to before[] index: before has extra context + pattern candles
    const idx = patternData.before.length + offset
    return patternData.before[idx] ?? null
  }

  // Only lookback positions for criteria (not context candles)
  const positions = useMemo(() => {
    if (!patternData) return []
    const pos = []
    for (let i = 0; i < patternCount; i++) {
      pos.push(-(patternCount - i))
    }
    pos.push(0)
    return pos
  }, [patternData, patternCount])

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
          <button className="stab-btn stab-btn--primary" onClick={() => setShowSaveDialog(true)}
            disabled={!patternData || activeCriteriaCount === 0}>Save</button>
          <button className="stab-btn stab-btn--primary" onClick={openLoadDialog}>Load</button>
          <button className="stab-btn stab-btn--primary" onClick={() => setShowDiscovery(true)}>Discover</button>
          <button className="stab-btn stab-btn--primary" onClick={toggleRefPanel}
            style={showRefPanel ? { borderColor: 'var(--amber)', color: 'var(--amber)', background: 'rgba(255,170,0,0.08)' } : {}}>
            Refs{refCount > 0 ? ` (${refCount})` : ''}
          </button>
        </div>
      </div>

      {/* Save Pattern Dialog */}
      {showSaveDialog && (
        <div className="pm-dialog-overlay" onClick={() => setShowSaveDialog(false)}>
          <div className="pm-dialog" onClick={e => e.stopPropagation()}>
            <h3>SAVE PATTERN</h3>
            <label className="pm-label">
              <span>Name</span>
              <input type="text" value={saveName} onChange={e => setSaveName(e.target.value)}
                className="pm-input pm-input--wide" placeholder="Pattern name..." autoFocus />
            </label>
            <label className="pm-label" style={{ marginTop: 8 }}>
              <span>Description</span>
              <input type="text" value={saveDesc} onChange={e => setSaveDesc(e.target.value)}
                className="pm-input pm-input--wide" placeholder="Optional description..." />
            </label>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button className="stab-btn stab-btn--accent" onClick={saveCurrentPattern}>Save</button>
              <button className="stab-btn stab-btn--primary" onClick={() => setShowSaveDialog(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* Load Pattern Dialog */}
      {showLoadDialog && (
        <div className="pm-dialog-overlay" onClick={() => setShowLoadDialog(false)}>
          <div className="pm-dialog pm-dialog--wide" onClick={e => e.stopPropagation()}>
            <h3>LOAD PATTERN</h3>
            {loadingPatterns ? (
              <div style={{ color: 'var(--text-dim)', padding: 12 }}>Loading...</div>
            ) : savedPatterns.length === 0 ? (
              <div style={{ color: 'var(--text-dim)', padding: 12 }}>No saved patterns</div>
            ) : (
              <div className="pm-pattern-list">
                {savedPatterns.map(pat => (
                  <div key={pat.id} className="pm-pattern-item">
                    <div className="pm-pattern-info">
                      <span className="pm-pattern-name">{pat.name}</span>
                      <span className="pm-pattern-meta">
                        {pat.tf} | LB:{pat.lookback} | {pat.criteria?.length || 0} pos
                        {pat.last_match_count != null && ` | ${pat.last_match_count} matches`}
                        {pat.last_match_win_rate != null && ` (WR: ${pat.last_match_win_rate.toFixed(1)}%)`}
                      </span>
                      {pat.description && <span className="pm-pattern-desc">{pat.description}</span>}
                    </div>
                    <div className="pm-pattern-actions">
                      <button className="stab-btn stab-btn--accent" onClick={() => loadSavedPattern(pat)}>Load</button>
                      <button className="stab-btn stab-btn--red" onClick={() => deleteSavedPattern(pat)}>Del</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <button className="stab-btn stab-btn--primary" onClick={() => setShowLoadDialog(false)}
              style={{ marginTop: 12 }}>Close</button>
          </div>
        </div>
      )}

      {/* Live Signals */}
      {signals?.length > 0 && (
        <div className="pm-signals">
          <h3>LIVE SIGNALS ({signals.length})</h3>
          <div className="pm-signal-list">
            {signals.map(s => (
              <div key={s.id} className="pm-signal-item">
                <span className="pm-signal-name">{s.pattern_name}</span>
                <span className={`pm-signal-dir ${s.direction_bias === 'LONG' ? 'up' : s.direction_bias === 'SHORT' ? 'down' : ''}`}>
                  {s.direction_bias || 'NEUTRAL'}
                </span>
                {s.match_score != null && (
                  <span className={`pm-score ${s.match_score >= 80 ? 'high' : s.match_score >= 50 ? 'mid' : 'low'}`}>
                    {s.match_score.toFixed(0)}%
                  </span>
                )}
                <span className="pm-signal-price">${s.price_at_signal?.toFixed(1)}</span>
                <span className="pm-signal-tf">{s.tf}</span>
                <span className="pm-signal-time">{fmtTs(s.match_ts)?.slice(11, 16)}</span>
                <button className="stab-btn stab-btn--red" style={{ padding: '2px 6px', fontSize: '9px' }}
                  onClick={() => dismissSignal(s.id)}>X</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reference Library Panel */}
      {showRefPanel && (
        <div className="pm-ref-panel">
          <div className="pm-ref-panel-header">
            <span>REFERENZ-BIBLIOTHEK ({references.length})</span>
            <button className="pm-active-remove" onClick={() => setShowRefPanel(false)}>&times;</button>
          </div>
          {loadingRefs ? (
            <div style={{ color: 'var(--text-dim)', padding: 8, fontFamily: 'var(--mono)', fontSize: 11 }}>Loading...</div>
          ) : references.length === 0 ? (
            <div style={{ color: 'var(--text-dim)', padding: 8, fontFamily: 'var(--mono)', fontSize: 11 }}>
              Keine Referenzen — klicke REF auf einer Kerze zum Speichern
            </div>
          ) : (
            <div className="pm-ref-grid">
              {references.map(ref => {
                const o = ref.outcome || {}
                const dir = o.direction || '—'
                const dirClass = dir === 'LONG' ? 'up' : dir === 'SHORT' ? 'down' : ''
                const pnl5 = o.pnl_5c
                const pnl10 = o.pnl_10c
                const pnl20 = o.pnl_20c
                const maxFav = o.max_favorable
                const maxAdv = o.max_adverse
                return (
                  <div key={ref.id} className="pm-ref-card">
                    <div className="pm-ref-card-top">
                      <span className="pm-ref-card-name" onClick={() => loadReference(ref)}
                        title="Klick zum Laden">{ref.name}</span>
                      <span className={`pm-ref-card-dir ${dirClass}`}>{dir}</span>
                      <span className="pm-ref-card-feats">{ref.features_count}F</span>
                    </div>
                    <div className="pm-ref-card-outcomes">
                      {pnl5 != null && (
                        <span className={`pm-ref-outcome-badge ${pnl5 >= 0 ? 'profit' : 'loss'}`}>
                          5c: {pnl5.toFixed(3)}%
                        </span>
                      )}
                      {pnl10 != null && (
                        <span className={`pm-ref-outcome-badge ${pnl10 >= 0 ? 'profit' : 'loss'}`}>
                          10c: {pnl10.toFixed(3)}%
                        </span>
                      )}
                      {pnl20 != null && (
                        <span className={`pm-ref-outcome-badge ${pnl20 >= 0 ? 'profit' : 'loss'}`}>
                          20c: {pnl20.toFixed(3)}%
                        </span>
                      )}
                      {maxFav != null && (
                        <span className="pm-ref-outcome-badge profit">MF: {maxFav.toFixed(3)}%</span>
                      )}
                      {maxAdv != null && (
                        <span className="pm-ref-outcome-badge loss">MA: {maxAdv.toFixed(3)}%</span>
                      )}
                    </div>
                    <div className="pm-ref-card-actions">
                      <button className="stab-btn stab-btn--accent" style={{ fontSize: '9px', padding: '2px 8px' }}
                        onClick={() => loadReference(ref)}>Load</button>
                      <button className="stab-btn stab-btn--primary" style={{ fontSize: '9px', padding: '2px 8px' }}
                        disabled={scanningRef === ref.id}
                        onClick={() => scanReference(ref)}>
                        {scanningRef === ref.id ? 'Scan...' : 'Scan'}
                      </button>
                      <span className="pm-ref-card-time">{ref.created_at?.slice(5, 16)}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {patternData && (
        <div className="pm-body">
          {/* Candle Slots */}
          <div className="pm-slots">
            {positions.map(offset => {
              const candle = getCandleAtOffset(offset)
              const isMeta = offset === 0
              const cc = Object.values(criteria[offset] || {}).filter(s => s.enabled).length
              return (
                <CandleSlot
                  key={offset}
                  candle={candle}
                  label={isMeta ? 'META' : String(offset)}
                  selected={selectedPos === offset}
                  isMeta={isMeta}
                  criteriaCount={cc}
                  onClick={() => setSelectedPos(offset)}
                  onSaveRef={saveReference}
                />
              )
            })}
          </div>

          {/* Position selector badges */}
          <div className="pm-pos-bar">
            {positions.map(offset => {
              const criteriaCount = Object.values(criteria[offset] || {}).filter(s => s.enabled).length
              const isMeta = offset === 0
              return (
                <button key={offset}
                  className={`pm-pos-btn ${selectedPos === offset ? 'selected' : ''} ${isMeta ? 'meta' : ''}`}
                  onClick={() => setSelectedPos(offset)}>
                  {isMeta ? 'META' : offset}
                  {criteriaCount > 0 && <span className="pm-pos-badge">{criteriaCount}</span>}
                </button>
              )
            })}
          </div>

          {/* Smart Criteria Editor for selected position */}
          {selectedPos !== null && (
            <SmartCriteriaEditor
              selectedPos={selectedPos}
              criteria={criteria}
              getCandleAtOffset={getCandleAtOffset}
              toggleFeature={toggleFeature}
              updateCriterion={updateCriterion}
              fmtTs={fmtTs}
            />
          )}

          {/* AI Chat Panel */}
          <ChatPanel
            metaTs={metaTs}
            tf={tf}
            lookback={lookback}
            criteria={criteria}
            getCandleAtOffset={getCandleAtOffset}
            onAction={handleChatAction}
          />
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

          {/* Outcome Overlay Chart */}
          {results.matches.some(m => m.after?.length > 0) && (
            <OutcomeOverlay matches={results.matches} />
          )}

          {/* Match list */}
          <div className="pm-match-list">
            <table className="pm-match-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Score</th>
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
                  const scoreClass = m.score >= 80 ? 'high' : m.score >= 50 ? 'mid' : 'low'
                  return [
                    <tr key={i} className={`pm-match-row ${expanded ? 'expanded' : ''}`}
                      onClick={() => setExpandedMatch(expanded ? null : i)}>
                      <td>{i + 1}</td>
                      <td><span className={`pm-score ${scoreClass}`}>{m.score != null ? `${m.score}%` : '—'}</span></td>
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
                        <td colSpan={includeWhales ? 11 : 10}>
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

      {/* Discovery Dialog */}
      {showDiscovery && (
        <div className="pm-dialog-overlay" onClick={() => setShowDiscovery(false)}>
          <div className="pm-dialog pm-dialog--wide" onClick={e => e.stopPropagation()}>
            <h3>AUTO-DISCOVERY</h3>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <button className="stab-btn stab-btn--accent" onClick={runDiscovery}
                disabled={discovering}>
                {discovering ? 'Scanning...' : 'Run Discovery'}
              </button>
              <button className="stab-btn stab-btn--primary"
                onClick={() => setShowDiscovery(false)}>Close</button>
            </div>
            {discoveries.length > 0 && (
              <div className="pm-discoveries">
                {discoveries.map((disc, i) => (
                  <div key={i} className="pm-disc-card">
                    <div className="pm-disc-header">
                      <span className={`pm-disc-dir ${disc.direction === 'LONG' ? 'up' : disc.direction === 'SHORT' ? 'down' : ''}`}>
                        {disc.direction}
                      </span>
                      <span className="pm-disc-wr">WR: {disc.win_rate}%</span>
                      <span className="pm-disc-size">n={disc.size}</span>
                      <span className="pm-disc-pnl" style={{ color: disc.avg_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        Avg: {disc.avg_pnl.toFixed(3)}%
                      </span>
                    </div>
                    <div className="pm-disc-features">
                      {disc.top_features?.map((f, j) => (
                        <span key={j} className="pm-disc-feat">{f.name}: {typeof f.value === 'number' ? f.value.toFixed(3) : f.value}</span>
                      ))}
                    </div>
                    <div className="pm-disc-stats">
                      <span>Edge: {disc.edge_quality.toFixed(0)}</span>
                      <span>Long: {disc.long_pct}%</span>
                      <span>Criteria: {disc.suggested_criteria?.length || 0}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
                      <button className="stab-btn stab-btn--accent" style={{ fontSize: '9px', padding: '3px 8px' }}
                        onClick={() => useDiscovery(disc)}>Use as Pattern</button>
                      <button className="stab-btn stab-btn--primary" style={{ fontSize: '9px', padding: '3px 8px' }}
                        onClick={() => saveDiscovery(disc)}>Save</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Inline operator controls for a single active criterion ─── */
function CriteriaControls({ selectedPos, feat, spec, updateCriterion }) {
  const op = spec?.op || '='
  return (
    <div className="pm-feat-controls">
      <select className="pm-op-select" value={op}
        onChange={e => updateCriterion(selectedPos, feat, 'op', e.target.value)}>
        {OPERATOR_OPTIONS.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {op === 'bool' && (
        <select className="pm-bool-select" value={spec.value ? 1 : 0}
          onChange={e => updateCriterion(selectedPos, feat, 'value', +e.target.value)}>
          <option value={1}>1 (true)</option>
          <option value={0}>0 (false)</option>
        </select>
      )}
      {op === '=' && (
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
      {op === 'range' && (
        <div className="pm-feat-tol">
          <input type="number" step="any" value={spec.min ?? 0}
            onChange={e => updateCriterion(selectedPos, feat, 'min', +e.target.value)}
            className="pm-tol-input" placeholder="min" />
          <span className="pm-tol-pm">&mdash;</span>
          <input type="number" step="any" value={spec.max ?? 0}
            onChange={e => updateCriterion(selectedPos, feat, 'max', +e.target.value)}
            className="pm-tol-input" placeholder="max" />
        </div>
      )}
      {['>', '<', '>=', '<='].includes(op) && (
        <input type="number" step="any" value={spec.value}
          onChange={e => updateCriterion(selectedPos, feat, 'value', +e.target.value)}
          className="pm-tol-input" />
      )}
    </div>
  )
}

/* ── Smart Criteria Editor: Active Panel + Feature Catalog ─── */
function SmartCriteriaEditor({ selectedPos, criteria, getCandleAtOffset, toggleFeature, updateCriterion, fmtTs }) {
  const [expandedGroups, setExpandedGroups] = useState({})
  const [search, setSearch] = useState('')

  const candle = getCandleAtOffset(selectedPos)
  const posCriteria = criteria[selectedPos] || {}

  // All active features for this position
  const activeFeats = useMemo(() =>
    Object.entries(posCriteria).filter(([, spec]) => spec.enabled).map(([feat]) => feat),
    [posCriteria]
  )

  // Search filter
  const searchLower = search.toLowerCase()
  const matchesSearch = (feat) => {
    if (!search) return true
    const meta = FEATURE_META[feat]
    return feat.toLowerCase().includes(searchLower) ||
      (meta?.desc || '').toLowerCase().includes(searchLower)
  }

  // Catalog groups (exclude raw, only matchable features)
  const catalogGroups = useMemo(() => {
    const groups = []
    for (const g of GROUPS) {
      if (g.id === 'raw') continue
      const feats = g.features.filter(f =>
        MATCHABLE_FEATURES.includes(f) && matchesSearch(f)
      )
      if (feats.length > 0) {
        const activeCount = feats.filter(f => posCriteria[f]?.enabled).length
        groups.push({ ...g, matchableFeats: feats, activeCount })
      }
    }
    return groups
  }, [search, posCriteria])

  const toggleGroup = (gid) => {
    setExpandedGroups(prev => ({ ...prev, [gid]: !prev[gid] }))
  }

  // Auto-expand groups with matching search
  const isExpanded = (gid) => {
    if (search) return true
    return !!expandedGroups[gid]
  }

  const ContextTag = ({ context }) => {
    const info = CONTEXT_LABELS[context]
    if (!info) return null
    return <span className="pm-feat-ctx" style={{ color: info.color }}>[{info.label}]</span>
  }

  return (
    <div className="pm-criteria">
      <h3>
        CRITERIA — Position {selectedPos === 0 ? 'META (0)' : selectedPos}
        <span className="pm-criteria-sub">
          {fmtTs(candle?.timestamp)}
        </span>
      </h3>

      {/* A) Active Criteria Panel */}
      {activeFeats.length > 0 && (
        <div className="pm-active-criteria">
          <div className="pm-active-header">ACTIVE ({activeFeats.length})</div>
          {activeFeats.map(feat => {
            const spec = posCriteria[feat]
            const val = candle?.[feat]
            const meta = FEATURE_META[feat]
            const group = featureToGroup[feat]
            const ctxInfo = CONTEXT_LABELS[meta?.context]
            return (
              <div key={feat} className="pm-active-row">
                <span className="pm-active-name" style={{ color: group?.color }}>{feat}</span>
                {ctxInfo && <span className="pm-active-ctx" style={{ color: ctxInfo.color }}>[{ctxInfo.label}]</span>}
                <span className="pm-active-desc">{meta?.desc || ''}</span>
                <span className="pm-active-val">{fmt(val, feat)}</span>
                <CriteriaControls
                  selectedPos={selectedPos}
                  feat={feat}
                  spec={spec}
                  updateCriterion={updateCriterion}
                />
                <button className="pm-active-remove" onClick={() => toggleFeature(selectedPos, feat)}
                  title="Remove criterion">&times;</button>
              </div>
            )
          })}
        </div>
      )}

      {/* B) Feature Catalog */}
      <div className="pm-catalog">
        <input
          type="text"
          className="pm-search-input"
          placeholder="Features suchen..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />

        {catalogGroups.map(g => (
          <div key={g.id} className="pm-catalog-group">
            <button
              className={`pm-catalog-group-header ${isExpanded(g.id) ? 'expanded' : ''}`}
              style={{ borderLeftColor: g.color }}
              onClick={() => toggleGroup(g.id)}
            >
              <span className="pm-catalog-group-name" style={{ color: g.color }}>{g.name}</span>
              <span className="pm-catalog-group-count">{g.matchableFeats.length}</span>
              {g.activeCount > 0 && <span className="pm-catalog-group-active">{g.activeCount} aktiv</span>}
              <span className="pm-catalog-chevron">{isExpanded(g.id) ? '\u25B4' : '\u25BE'}</span>
            </button>

            {isExpanded(g.id) && (
              <div className="pm-catalog-features">
                {g.matchableFeats.map(feat => {
                  const isActive = posCriteria[feat]?.enabled
                  const val = candle?.[feat]
                  const meta = FEATURE_META[feat]
                  return (
                    <div key={feat} className={`pm-feat-row ${isActive ? 'active' : ''}`}>
                      <label className="pm-feat-toggle">
                        <input type="checkbox" checked={!!isActive}
                          onChange={() => toggleFeature(selectedPos, feat)} />
                        <span className="pm-feat-name">{feat}</span>
                      </label>
                      <ContextTag context={meta?.context} />
                      <span className="pm-feat-desc-inline" title={meta?.desc}>{meta?.desc}</span>
                      <span className="pm-feat-val" style={{ color: g.color }}>{fmt(val, feat)}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        ))}
      </div>
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
