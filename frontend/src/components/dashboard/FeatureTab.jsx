import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { api } from '../../api/client'
import GROUPS, { ALL_FEATURE_NAMES } from '../../lib/featureGroups'
import './FeatureTab.css'

function fmtVal(v) {
  if (v == null) return 'NULL'
  if (typeof v === 'string') return v.length > 120 ? v.slice(0, 120) + '…' : v
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v)
    if (Math.abs(v) >= 1000) return v.toFixed(2)
    return v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '.0')
  }
  return String(v)
}

function fmtTs(ts) {
  if (!ts) return '--'
  return new Date(ts).toLocaleString('sv-SE', { timeZone: 'Europe/Berlin' })
}

function formatFeatureName(name) {
  return name.replace(/_/g, ' ')
}

function toNum(v, fallback = 0) {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

function clamp01(v) {
  if (v <= 0) return 0
  if (v >= 1) return 1
  return v
}

function hasFlag(c, key) {
  return toNum(c[key], 0) > 0
}

function isZeroLike(v) {
  if (v == null) return true
  if (typeof v === 'number') return v === 0
  if (typeof v === 'string') return v === '' || v === '[]' || v === '{}'
  return false
}

function gradeBucket(score) {
  if (score >= 80) return 'S'
  if (score >= 65) return 'A'
  if (score >= 50) return 'B'
  if (score >= 35) return 'C'
  return 'D'
}

function computeCandleGrade(candle) {
  if (!candle) return { score: 0, bucket: 'D', label: 'D 0', reasons: [] }

  let score = 0
  const reasons = []
  const pushReason = (txt) => {
    if (reasons.length < 4) reasons.push(txt)
  }

  const eventWeights = [
    ['bull_div', 24, 'MACD Bull Div'],
    ['bear_div', 24, 'MACD Bear Div'],
    ['choch', 14, 'CHoCH'],
    ['is_seeker_kill', 13, 'Seeker Kill'],
    ['bos_bull', 11, 'BOS Bull'],
    ['bos_bear', 11, 'BOS Bear'],
    ['broken_was_seeker', 9, 'Breaker from Seeker'],
    ['is_seeker_div', 6, 'Seeker Div'],
    ['candle_was_seeker', 6, 'Seeker Candle'],
    ['candle_was_seeker_div', 5, 'Seeker Div Candle'],
    ['htf_bos', 7, 'HTF BOS'],
    ['whale_cluster', 10, 'Whale Cluster'],
    ['elite_whale_active', 14, 'Elite Whale'],
  ]

  for (const [key, weight, reason] of eventWeights) {
    if (hasFlag(candle, key)) {
      score += weight
      if (weight >= 10) pushReason(reason)
    }
  }

  const divStrengthBoost = clamp01(toNum(candle.div_strength) / 0.35)
  const divStreakBoost = clamp01((Math.max(toNum(candle.bull_div_streak), toNum(candle.bear_div_streak)) - 1) / 3.0)
  const breakDepthBoost = clamp01(toNum(candle.break_depth) / 1.2)
  const volBoost = clamp01((toNum(candle.vol_vs_ma) - 1.0) / 1.6)
  const deltaBoost = clamp01(Math.abs(toNum(candle.delta_pct)) / 0.8)
  const emaDistBoost = clamp01(Math.abs(toNum(candle.ema21_dist)) / 3.2)
  const atrPctBoost = clamp01((toNum(candle.atr14) / Math.max(1, toNum(candle.close, 1))) / 0.0025)
  const seekerNrBoost = clamp01(toNum(candle.seeker_div_nr) / 8.0)
  const killedDivBoost = clamp01(toNum(candle.killed_seeker_divs) / 4.0)
  const killedCountBoost = clamp01(toNum(candle.killed_seekers_count) / 3.0)
  const whaleConfBoost = clamp01(toNum(candle.whale_confidence))
  const whaleClusterBoost = clamp01(toNum(candle.whale_cluster_strength))
  const pressureImbBoost = clamp01(Math.abs(toNum(candle.bull_pressure) - toNum(candle.bear_pressure)) / 0.35)

  score += divStrengthBoost * 10
  score += divStreakBoost * 6
  score += breakDepthBoost * 8
  score += volBoost * 9
  score += deltaBoost * 8
  score += atrPctBoost * 6
  score += emaDistBoost * 5
  score += seekerNrBoost * 6
  score += killedDivBoost * 6
  score += killedCountBoost * 6
  score += whaleConfBoost * 8
  score += whaleClusterBoost * 8
  score += pressureImbBoost * 6

  if (volBoost > 0.7) pushReason('High Relative Volume')
  if (deltaBoost > 0.7) pushReason('Strong Delta Imbalance')
  if (divStrengthBoost > 0.55) pushReason('Strong Divergence Intensity')
  if (divStreakBoost > 0.2) pushReason(`Div Stack x${Math.max(toNum(candle.bull_div_streak), toNum(candle.bear_div_streak))}`)
  if (breakDepthBoost > 0.55) pushReason('Deep Structure Break')

  const hasBullDiv = hasFlag(candle, 'bull_div')
  const hasBearDiv = hasFlag(candle, 'bear_div')
  const hasChoch = hasFlag(candle, 'choch')
  const hasBosBull = hasFlag(candle, 'bos_bull')
  const hasBosBear = hasFlag(candle, 'bos_bear')
  const hasSeekerKill = hasFlag(candle, 'is_seeker_kill')

  if ((hasBullDiv && hasBosBear) || (hasBearDiv && hasBosBull)) {
    score += 8
    pushReason('Divergence + Opposite BOS')
  }
  if ((hasBullDiv || hasBearDiv) && hasChoch) {
    score += 8
    pushReason('Divergence + CHoCH')
  }
  if (hasSeekerKill && (hasBullDiv || hasBearDiv)) {
    score += 6
    pushReason('Seeker Trap Release')
  }
  if (volBoost > 0.7 && deltaBoost > 0.7 && (hasBosBull || hasBosBear || hasChoch)) {
    score += 6
    pushReason('Momentum + Structure Alignment')
  }
  if (killedDivBoost > 0.6 && hasSeekerKill) {
    score += 5
    pushReason('Multi-Seeker Kill Pressure')
  }

  let families = 0
  if (hasBullDiv || hasBearDiv || divStrengthBoost > 0.4) families++
  if (hasBosBull || hasBosBear || hasChoch || breakDepthBoost > 0.4) families++
  if (hasFlag(candle, 'is_seeker_div') || hasFlag(candle, 'is_seeker_div_hs') || hasFlag(candle, 'is_seeker_div_ls') || hasSeekerKill || seekerNrBoost > 0.3) families++
  if (volBoost > 0.3 || deltaBoost > 0.3 || atrPctBoost > 0.3) families++
  if (whaleConfBoost > 0.2 || whaleClusterBoost > 0.2 || hasFlag(candle, 'elite_whale_active')) families++
  score += Math.max(0, families - 1) * 1.5

  score = Math.min(100, Math.max(0, score))
  const rounded = Math.round(score)
  const bucket = gradeBucket(rounded)
  return {
    score: rounded,
    bucket,
    label: `${bucket} ${rounded}`,
    reasons,
    interesting: rounded >= 50,
  }
}

function valueTone(name, value) {
  if (value == null) return 'null'
  if (typeof value === 'number') {
    if (value === 0) return 'inactive'
    if (
      name.startsWith('is_') ||
      name === 'bos_bull' ||
      name === 'bos_bear' ||
      name === 'choch' ||
      name.endsWith('_kill') ||
      name.endsWith('_div') ||
      name.endsWith('_peak') ||
      name.endsWith('_trough')
    ) {
      return value > 0 ? 'active' : 'inactive'
    }
    if (name.includes('bull') || name === 'is_bullish') return value > 0 ? 'bull' : 'inactive'
    if (name.includes('bear')) return value > 0 ? 'bear' : 'inactive'
    return value > 0 ? 'positive' : 'negative'
  }
  if (typeof value === 'string') return value.length ? 'text' : 'null'
  return 'text'
}

function boolChip(label, tone = 'active') {
  return { label, tone }
}

function numericChip(label, value, suffix = '') {
  return { label: `${label} ${fmtVal(value)}${suffix}`, tone: 'neutral' }
}

function groupSummary(group, candle) {
  if (!candle) return []
  const chips = []

  for (const key of group.summary || []) {
    const value = candle[key]
    if (value == null) continue
    if (typeof value === 'number' && value === 0 && key !== 'close') continue
    if (key === 'close') {
      chips.push(numericChip('Close', value))
      continue
    }
    if (key === 'timestamp') {
      chips.push({ label: fmtTs(value), tone: 'neutral' })
      continue
    }
    if (
      key.startsWith('is_') ||
      key === 'bos_bull' ||
      key === 'bos_bear' ||
      key === 'choch' ||
      key.endsWith('_kill') ||
      key.endsWith('_div')
    ) {
      if (toNum(value) > 0) {
        chips.push(boolChip(formatFeatureName(key)))
      }
      continue
    }
    chips.push({
      label: `${formatFeatureName(key)} ${fmtVal(value)}`,
      tone: valueTone(key, value),
    })
  }

  if (chips.length === 0) {
    chips.push({ label: 'No active signals', tone: 'inactive' })
  }
  return chips.slice(0, 5)
}

function eventLabels(candle) {
  const labels = []
  if (candle.bos_bull) labels.push({ label: 'BOS+', tone: 'bull' })
  if (candle.bos_bear) labels.push({ label: 'BOS-', tone: 'bear' })
  if (candle.choch) labels.push({ label: 'CHoCH', tone: 'neutral' })
  if (candle.is_seeker_hs) labels.push({ label: 'HS', tone: 'bear' })
  if (candle.is_seeker_ls) labels.push({ label: 'LS', tone: 'bull' })
  if (candle.is_seeker_div_hs) labels.push({ label: `HS Div #${Math.max(1, toNum(candle.seeker_div_nr))}`, tone: 'bear' })
  if (candle.is_seeker_div_ls) labels.push({ label: `LS Div #${Math.max(1, toNum(candle.seeker_div_nr))}`, tone: 'bull' })
  if (candle.is_seeker_kill) labels.push({ label: `Kill x${Math.max(1, toNum(candle.killed_seekers_count, 1))}`, tone: 'warning' })
  if (candle.bull_div) labels.push({ label: Math.max(1, toNum(candle.bull_div_streak)) > 1 ? `Bull Div x${Math.max(1, toNum(candle.bull_div_streak))}` : 'Bull Div', tone: 'bull' })
  if (candle.bear_div) labels.push({ label: Math.max(1, toNum(candle.bear_div_streak)) > 1 ? `Bear Div x${Math.max(1, toNum(candle.bear_div_streak))}` : 'Bear Div', tone: 'bear' })
  return labels
}

function chipClass(tone = 'neutral') {
  return `ftab-chip ftab-chip--${tone}`
}

function deriveCyclePrimary(context) {
  if (!context) return null
  if (context.containingOpenCycles?.length) return context.containingOpenCycles[0]
  if (context.containingKilledCycles?.length) return context.containingKilledCycles[0]
  if (context.openCycles?.length) return context.openCycles[0]
  if (context.killedCycles?.length) return context.killedCycles[0]
  return null
}

function cycleAgeLabel(cycle) {
  if (!cycle) return '--'
  const bars = toNum(cycle.age_bars)
  const hours = toNum(cycle.age_ms) / 3600000
  return `${bars} bars · ${hours.toFixed(1)}h`
}

function zoneDistanceLabel(cycle, price) {
  if (!cycle || price == null) return '--'
  const top = toNum(cycle.zone_top)
  const bottom = toNum(cycle.zone_bottom)
  if (price >= bottom && price <= top) return 'inside zone'
  if (price < bottom) return `${(bottom - price).toFixed(2)} below`
  return `${(price - top).toFixed(2)} above`
}

function ZoneCard({ title, cycle, price }) {
  if (!cycle) {
    return (
      <div className="ftab-cycle-mini ftab-cycle-mini--empty">
        <div className="ftab-cycle-mini-title">{title}</div>
        <div className="ftab-cycle-mini-body">none</div>
      </div>
    )
  }

  return (
    <div className="ftab-cycle-mini">
      <div className="ftab-cycle-mini-title">{title}</div>
      <div className="ftab-cycle-mini-body">
        <span className={chipClass(cycle.cycle_type === 'LS' ? 'bull' : 'bear')}>
          {cycle.cycle_type} {cycle.status}
        </span>
        <span>{fmtTs(cycle.origin_ts)}</span>
        <span>{toNum(cycle.zone_bottom).toFixed(2)} → {toNum(cycle.zone_top).toFixed(2)}</span>
        <span>{zoneDistanceLabel(cycle, price)}</span>
      </div>
    </div>
  )
}

function CycleContextBox({ cycleContext, candle }) {
  const primary = deriveCyclePrimary(cycleContext)
  const currentPrice = candle ? toNum(candle.close) : null

  return (
    <div className="ftab-cycle-context">
      <div className="ftab-cycle-header">
        <div>
          <div className="ftab-section-kicker">Cycle Context</div>
          <h4>Persisted seeker cycle state</h4>
        </div>
        <div className="ftab-cycle-status-row">
          <span className={chipClass(primary ? (primary.status === 'open' ? 'active' : 'warning') : 'inactive')}>
            {primary ? `${primary.cycle_type} ${primary.status}` : 'no linked cycle'}
          </span>
          {primary && <span className={chipClass(primary.cycle_type === 'LS' ? 'bull' : 'bear')}>{cycleAgeLabel(primary)}</span>}
        </div>
      </div>

      <div className="ftab-cycle-grid">
        <div className="ftab-cycle-card">
          <div className="ftab-cycle-card-title">Primary Cycle</div>
          {primary ? (
            <div className="ftab-cycle-card-body">
              <div><strong>Origin</strong> {fmtTs(primary.origin_ts)}</div>
              <div><strong>Zone</strong> {toNum(primary.zone_bottom).toFixed(2)} → {toNum(primary.zone_top).toFixed(2)}</div>
              <div><strong>Divs</strong> {toNum(primary.div_count_total)}</div>
              <div><strong>Age</strong> {cycleAgeLabel(primary)}</div>
              <div><strong>Time to first div</strong> {primary.time_to_first_div_bars ?? '--'} bars</div>
              <div><strong>Time to kill</strong> {primary.time_to_kill_bars ?? '--'} bars</div>
            </div>
          ) : (
            <div className="ftab-cycle-card-body ftab-cycle-card-body--empty">No active or historical cycle anchored to this candle yet.</div>
          )}
        </div>

        <div className="ftab-cycle-card">
          <div className="ftab-cycle-card-title">Nearest Zones</div>
          <div className="ftab-cycle-mini-list">
            <ZoneCard title="Open HS above" cycle={cycleContext?.nearestOpenHsAbove} price={currentPrice} />
            <ZoneCard title="Open LS below" cycle={cycleContext?.nearestOpenLsBelow} price={currentPrice} />
            <ZoneCard title="Killed HS above" cycle={cycleContext?.nearestKilledHsAbove} price={currentPrice} />
            <ZoneCard title="Killed LS below" cycle={cycleContext?.nearestKilledLsBelow} price={currentPrice} />
          </div>
        </div>
      </div>
    </div>
  )
}

function CandleChart({ candles, centerTs }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !candles || candles.length === 0) return
    const ctx = canvas.getContext('2d')
    const dpr = window.devicePixelRatio || 1
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    canvas.width = w * dpr
    canvas.height = h * dpr
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)

    const pad = { top: 12, bottom: 28, left: 6, right: 54 }
    const chartW = w - pad.left - pad.right
    const chartH = h - pad.top - pad.bottom
    const n = candles.length

    let pMin = Infinity
    let pMax = -Infinity
    for (const c of candles) {
      if (c.low < pMin) pMin = c.low
      if (c.high > pMax) pMax = c.high
    }
    const pRange = pMax - pMin || 1
    pMin -= pRange * 0.04
    pMax += pRange * 0.04
    const pSpan = pMax - pMin

    const toY = (p) => pad.top + chartH * (1 - (p - pMin) / pSpan)
    const gap = chartW / n
    const candleW = Math.max(3, Math.floor(gap * 0.64))
    const toX = (i) => pad.left + gap * i + gap / 2
    const centerIdx = candles.findIndex(c => c.timestamp === centerTs)

    ctx.strokeStyle = 'rgba(0, 240, 255, 0.05)'
    ctx.lineWidth = 1
    for (let i = 0; i <= 4; i++) {
      const price = pMin + (pSpan * i / 4)
      const y = Math.round(toY(price)) + 0.5
      ctx.beginPath()
      ctx.moveTo(pad.left, y)
      ctx.lineTo(w - pad.right, y)
      ctx.stroke()
      ctx.fillStyle = '#556677'
      ctx.font = '9px monospace'
      ctx.textAlign = 'left'
      ctx.fillText(price.toFixed(0), w - pad.right + 4, y + 3)
    }

    for (let i = 0; i < n; i++) {
      const c = candles[i]
      const x = toX(i)
      const isC = i === centerIdx
      const isBull = c.close >= c.open
      const color = isBull ? '#00ff88' : '#ff3355'

      if (isC) {
        ctx.fillStyle = 'rgba(0, 240, 255, 0.06)'
        ctx.fillRect(x - gap / 2, pad.top, gap, chartH)
      }

      ctx.strokeStyle = isC ? color : (isBull ? 'rgba(0,255,136,0.5)' : 'rgba(255,51,85,0.5)')
      ctx.lineWidth = isC ? 2 : 1
      ctx.beginPath()
      ctx.moveTo(x, toY(c.high))
      ctx.lineTo(x, toY(c.low))
      ctx.stroke()

      const bTop = toY(Math.max(c.open, c.close))
      const bBot = toY(Math.min(c.open, c.close))
      const bH = Math.max(1, bBot - bTop)
      ctx.fillStyle = isC
        ? (isBull ? 'rgba(0,255,136,0.9)' : 'rgba(255,51,85,0.9)')
        : (isBull ? 'rgba(0,255,136,0.45)' : 'rgba(255,51,85,0.45)')
      ctx.fillRect(x - candleW / 2, bTop, candleW, bH)

      if (isC) {
        ctx.strokeStyle = '#00f0ff'
        ctx.lineWidth = 1.5
        ctx.strokeRect(x - candleW / 2 - 1, bTop - 1, candleW + 2, bH + 2)
      }

      const ev = []
      if (c.bos_bull) ev.push({ l: 'B+', co: '#00ff88' })
      if (c.bos_bear) ev.push({ l: 'B-', co: '#ff3355' })
      if (c.choch) ev.push({ l: 'C', co: '#8fe6ff' })
      if (c.is_seeker_hs) ev.push({ l: 'HS', co: '#ff7a7a' })
      if (c.is_seeker_ls) ev.push({ l: 'LS', co: '#44ffaa' })
      if (c.is_seeker_div_hs) ev.push({ l: `DH${Math.max(1, toNum(c.seeker_div_nr))}`, co: '#ff9f43' })
      if (c.is_seeker_div_ls) ev.push({ l: `DL${Math.max(1, toNum(c.seeker_div_nr))}`, co: '#00d7ff' })
      if (c.is_seeker_kill) ev.push({ l: `K${Math.max(1, toNum(c.killed_seekers_count, 1))}`, co: '#ff00ff' })
      if (c.bull_div) ev.push({ l: Math.max(1, toNum(c.bull_div_streak)) > 1 ? `U${Math.max(1, toNum(c.bull_div_streak))}` : 'U', co: '#44ffaa' })
      if (c.bear_div) ev.push({ l: Math.max(1, toNum(c.bear_div_streak)) > 1 ? `D${Math.max(1, toNum(c.bear_div_streak))}` : 'D', co: '#aa44ff' })

      if (ev.length) {
        ctx.font = 'bold 7px monospace'
        ctx.textAlign = 'center'
        ev.slice(0, 3).forEach((e, ei) => {
          ctx.fillStyle = e.co
          ctx.fillText(e.l, x, h - pad.bottom + 9 + ei * 8)
        })
      }

      if (isC) {
        ctx.fillStyle = '#00f0ff'
        ctx.font = 'bold 8px monospace'
        ctx.textAlign = 'center'
        ctx.fillText(fmtTs(c.timestamp).slice(5), x, pad.top - 3)
      }
    }
  }, [candles, centerTs])

  return (
    <div className="ftab-chart-wrap">
      <canvas ref={canvasRef} className="ftab-chart-canvas" />
    </div>
  )
}

function FeatureRow({ name, value }) {
  const tone = valueTone(name, value)
  const dim = isZeroLike(value)
  return (
    <div className={`ftab-feat ${dim ? 'ftab-feat--dim' : ''}`}>
      <span className="ftab-feat-name">{formatFeatureName(name)}</span>
      <span className={`ftab-feat-val ftab-feat-val--${tone}`}>{fmtVal(value)}</span>
    </div>
  )
}

export default function FeatureTab({ tf, onUseAsPattern }) {
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(1)
  const [selectedTs, setSelectedTs] = useState(null)
  const [candle, setCandle] = useState(null)
  const [neighbors, setNeighbors] = useState(null)
  const [cycleContext, setCycleContext] = useState(null)
  const [tsInput, setTsInput] = useState('')
  const [sortMode, setSortMode] = useState('time')

  const loadPage = useCallback(async (p) => {
    const res = await api(`/api/db/candles?tf=${tf}&page=${p}&limit=30&order=desc`)
    if (res) {
      setData(res.candles)
      setPage(res.page)
      setTotal(res.total)
      setPages(res.pages)
    }
  }, [tf])

  useEffect(() => { loadPage(1) }, [loadPage])

  useEffect(() => {
    const id = setInterval(() => {
      if (page === 1) loadPage(1)
    }, 5000)
    return () => clearInterval(id)
  }, [page, loadPage])

  const gradedData = useMemo(() => {
    if (!data || data.length === 0) return []
    return data.map(c => ({ ...c, _grade: computeCandleGrade(c) }))
  }, [data])

  const tableRows = useMemo(() => {
    const rows = gradedData.slice()
    if (sortMode === 'grade') {
      rows.sort((a, b) => {
        if (b._grade.score !== a._grade.score) return b._grade.score - a._grade.score
        return b.timestamp - a.timestamp
      })
    } else {
      rows.sort((a, b) => b.timestamp - a.timestamp)
    }
    return rows
  }, [gradedData, sortMode])

  const loadCandle = useCallback(async (ts) => {
    const [neighborRes, cycleRes] = await Promise.all([
      api(`/api/db/candle/${ts}/neighbors?tf=${tf}&range=12`),
      api(`/api/seeker-cycles/current?tf=${tf}&ts=${ts}`),
    ])
    if (neighborRes?.center) {
      setCandle(neighborRes.center)
      setSelectedTs(ts)
      setNeighbors([...(neighborRes.before || []), neighborRes.center, ...(neighborRes.after || [])])
      setCycleContext(cycleRes || null)
    }
  }, [tf])

  const handleGo = () => { if (tsInput) loadCandle(+tsInput) }
  const handleLatest = () => { if (gradedData.length > 0) loadCandle(gradedData[0].timestamp) }

  const handlePrev = () => {
    if (!candle || tableRows.length === 0) return
    const idx = tableRows.findIndex(c => c.timestamp === candle.timestamp)
    if (idx >= 0 && idx < tableRows.length - 1) loadCandle(tableRows[idx + 1].timestamp)
    else if (page < pages) loadPage(page + 1)
  }

  const handleNext = () => {
    if (!candle || tableRows.length === 0) return
    const idx = tableRows.findIndex(c => c.timestamp === candle.timestamp)
    if (idx > 0) loadCandle(tableRows[idx - 1].timestamp)
    else if (page > 1) loadPage(page - 1)
  }

  const selectedGrade = useMemo(() => {
    if (!candle) return null
    return computeCandleGrade(candle)
  }, [candle])

  const featureCoverage = useMemo(() => {
    const groupFeatures = new Set(ALL_FEATURE_NAMES)
    const candleKeys = candle ? Object.keys(candle).filter(key => !key.startsWith('_') && key !== 'id') : []
    return candleKeys.filter(key => !groupFeatures.has(key)).sort()
  }, [candle])

  return (
    <div className="feature-tab panel">
      <div className="ftab-nav">
        <div>
          <h2>CANDLE INSPECTOR ({tf})</h2>
          <div className="ftab-subtitle">Full grouped candle state first. Interpretation comes after.</div>
        </div>
        <div className="ftab-controls">
          <button className="stab-btn stab-btn--ghost" onClick={handleLatest}>Latest</button>
          <button className="stab-btn stab-btn--ghost" onClick={handlePrev}>Prev</button>
          <button className="stab-btn stab-btn--ghost" onClick={handleNext}>Next</button>
          <input className="ftab-ts-input" type="text" placeholder="Timestamp"
            value={tsInput} onChange={e => setTsInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleGo()} />
          <button className="stab-btn stab-btn--primary" onClick={handleGo}>Go</button>
          <button
            className="stab-btn stab-btn--ghost"
            onClick={() => setSortMode(prev => (prev === 'time' ? 'grade' : 'time'))}
            title={sortMode === 'time' ? 'Sort candles by grade score' : 'Sort candles by timestamp'}
          >
            Sort: {sortMode === 'time' ? 'Time' : 'Grade'}
          </button>
          <span className="ftab-page">Page {page}/{pages} ({total} candles)</span>
          <button className="stab-btn stab-btn--ghost" onClick={() => loadPage(Math.max(1, page - 1))}
            disabled={page <= 1}>&#9664;</button>
          <button className="stab-btn stab-btn--ghost" onClick={() => loadPage(Math.min(pages, page + 1))}
            disabled={page >= pages}>&#9654;</button>
        </div>
      </div>

      <div className="ftab-body">
        <div className="ftab-table-wrap">
          <table className="ftab-table">
            <thead>
              <tr>
                <th>Grade</th>
                <th>Timestamp</th>
                <th>Close</th>
                <th>Volume</th>
                <th>Events</th>
              </tr>
            </thead>
            <tbody>
              {tableRows.map(c => {
                const events = eventLabels(c)
                return (
                  <tr
                    key={c.timestamp}
                    className={[
                      selectedTs === c.timestamp ? 'selected' : '',
                      c._grade?.interesting ? 'ftab-interesting' : '',
                    ].join(' ').trim()}
                    onClick={() => loadCandle(c.timestamp)}
                  >
                    <td>
                      <span className={`ftab-grade-chip grade-${(c._grade?.bucket || 'D').toLowerCase()}`} title="Grade summarizes the raw features shown on the right.">
                        {c._grade?.label || 'D 0'}
                      </span>
                    </td>
                    <td>{fmtTs(c.timestamp)}</td>
                    <td>{c.close?.toFixed(2)}</td>
                    <td>{c.volume?.toFixed(0)}</td>
                    <td className="ftab-events">
                      {events.length ? events.slice(0, 4).map(item => item.label).join(' · ') : '-'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <div className="ftab-right">
          {neighbors && neighbors.length > 0 && (
            <CandleChart candles={neighbors} centerTs={selectedTs} />
          )}

          <div className="ftab-features">
            {candle ? (
              <>
                <div className="ftab-candle-summary">
                  <div className="ftab-candle-summary-main">
                    <span className="ftab-cs-ts">{fmtTs(candle.timestamp)}</span>
                    {selectedGrade && (
                      <span className={`ftab-grade-chip grade-${selectedGrade.bucket.toLowerCase()}`} title="Grade is a compressed view of the raw features below.">
                        Grade {selectedGrade.label}
                      </span>
                    )}
                    <span className={chipClass(candle.is_bullish ? 'bull' : 'bear')}>
                      {candle.is_bullish ? 'bullish' : 'bearish'}
                    </span>
                    <span className="ftab-summary-ohlc">O {fmtVal(candle.open)} · H {fmtVal(candle.high)} · L {fmtVal(candle.low)} · C {fmtVal(candle.close)}</span>
                  </div>
                  <div className="ftab-candle-summary-events">
                    {eventLabels(candle).map(item => (
                      <span key={item.label} className={chipClass(item.tone)}>{item.label}</span>
                    ))}
                    {onUseAsPattern && (
                      <button className="stab-btn stab-btn--accent ftab-pattern-btn" onClick={() => onUseAsPattern(candle.timestamp)}>
                        USE AS PATTERN
                      </button>
                    )}
                  </div>
                </div>

                <CycleContextBox cycleContext={cycleContext} candle={candle} />

                {GROUPS.map(group => (
                  <div className="ftab-group" key={group.id}>
                    <div className="ftab-group-head">
                      <div>
                        <div className="ftab-section-kicker">Feature Group</div>
                        <h4 style={{ color: group.color }}>{group.name}</h4>
                      </div>
                      <div className="ftab-group-summary">
                        {groupSummary(group, candle).map(chip => (
                          <span key={`${group.id}-${chip.label}`} className={chipClass(chip.tone)}>{chip.label}</span>
                        ))}
                      </div>
                    </div>
                    <div className="ftab-group-grid">
                      {group.features.map(fname => (
                        <FeatureRow key={fname} name={fname} value={candle[fname]} />
                      ))}
                    </div>
                  </div>
                ))}

                {featureCoverage.length > 0 && (
                  <div className="ftab-group ftab-group--warning">
                    <div className="ftab-group-head">
                      <div>
                        <div className="ftab-section-kicker">Coverage Check</div>
                        <h4>Ungrouped Candle Fields</h4>
                      </div>
                      <span className={chipClass('warning')}>{featureCoverage.length} fields</span>
                    </div>
                    <div className="ftab-group-grid">
                      {featureCoverage.map(name => (
                        <FeatureRow key={name} name={name} value={candle[name]} />
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="ftab-empty">Select a candle to inspect every stored feature and cycle context.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
