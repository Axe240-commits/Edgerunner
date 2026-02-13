// Live Chart + Candle Disassemble/Reassemble Animation Engine
// Layout: Live candlestick chart (left) → Data bridge → Analysis zone with radial feature nodes (right)
// 6-second animation loop — only the LIVE candle animates

import { computeRadialPositions } from './radialLayout'

const DURATION = 6000
const CHART_RATIO = 0.55

const PHASES = {
  ASSEMBLED:      { start: 0.00, end: 0.15 },
  DISASSEMBLE:    { start: 0.15, end: 0.40 },
  ANALYSIS_PULSE: { start: 0.40, end: 0.70 },
  REASSEMBLE:     { start: 0.70, end: 0.90 },
  GLOW:           { start: 0.90, end: 1.00 },
}

function easeOut(t) { return 1 - Math.pow(1 - t, 3) }
function easeInOut(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2 }

function getPhase(progress) {
  for (const [name, { start, end }] of Object.entries(PHASES)) {
    if (progress >= start && progress < end) {
      return { name, local: (progress - start) / (end - start) }
    }
  }
  return { name: 'GLOW', local: 1 }
}

function hexToRgb(hex) {
  const h = hex.replace('#', '')
  return `${parseInt(h.substring(0, 2), 16)}, ${parseInt(h.substring(2, 4), 16)}, ${parseInt(h.substring(4, 6), 16)}`
}

// ===== Persistent state =====
let _dataPackets = null
let _bridgePackets = null
let _confidenceDisplay = 0
let _liveCandleLerp = null
let _lastCandleOpen = 0
let _emaDust = []
const MAX_DUST = 120
let _biasFog = []
const MAX_FOG = 60
let _biasSmooth = 0
let _magnetDust = []
const MAX_MAGNET = 80

function findSwingLevels(data, lookback = 3) {
  const levels = []
  for (let i = lookback; i < data.length - lookback; i++) {
    let isHigh = true, isLow = true
    for (let j = 1; j <= lookback; j++) {
      if (data[i].high <= data[i - j].high || data[i].high <= data[i + j].high) isHigh = false
      if (data[i].low >= data[i - j].low || data[i].low >= data[i + j].low) isLow = false
    }
    if (isHigh) levels.push({ price: data[i].high, type: 'high' })
    if (isLow) levels.push({ price: data[i].low, type: 'low' })
  }
  return levels
}

function getLerpedCandle(candles, tickerPrice) {
  const real = candles[candles.length - 1]

  // Use live ticker price as current close (updates every 2s)
  const liveClose = tickerPrice || real.close
  const liveHigh = Math.max(real.high, liveClose)
  const liveLow = Math.min(real.low, liveClose)

  // Detect new candle by open price change (not count — count stays at 200)
  const isNewCandle = !_liveCandleLerp || real.open !== _lastCandleOpen

  if (isNewCandle) {
    _lastCandleOpen = real.open
    _liveCandleLerp = {
      open: real.open,
      high: real.open,
      low: real.open,
      close: real.open,
    }
  }

  // Smooth lerp towards actual values (runs every frame at 60fps)
  const rate = 0.05
  _liveCandleLerp.close += (liveClose - _liveCandleLerp.close) * rate
  _liveCandleLerp.high += (liveHigh - _liveCandleLerp.high) * rate
  _liveCandleLerp.low += (liveLow - _liveCandleLerp.low) * rate

  // Ensure OHLC consistency
  _liveCandleLerp.high = Math.max(_liveCandleLerp.high, _liveCandleLerp.close, _liveCandleLerp.open)
  _liveCandleLerp.low = Math.min(_liveCandleLerp.low, _liveCandleLerp.close, _liveCandleLerp.open)

  return _liveCandleLerp
}

function computeATR(candles, period = 14) {
  const n = Math.min(period + 1, candles.length)
  if (n < 2) return 100
  const recent = candles.slice(-n)
  let sum = 0
  for (let i = 1; i < recent.length; i++) {
    sum += recent[i].high - recent[i].low
  }
  return sum / (recent.length - 1) || 100
}

function initDataPackets(nodeCount) {
  const packets = []
  for (let n = 0; n < nodeCount; n++) {
    const count = 3 + Math.floor(Math.random() * 3)
    for (let p = 0; p < count; p++) {
      packets.push({
        nodeIdx: n,
        offset: Math.random(),
        speed: 0.3 + Math.random() * 0.7,
        size: 1 + Math.random() * 2,
        brightness: 0.4 + Math.random() * 0.6,
        returning: Math.random() > 0.5,
      })
    }
  }
  return packets
}

function initBridgePackets() {
  const packets = []
  for (let i = 0; i < 15; i++) {
    packets.push({
      offset: Math.random(),
      speed: 0.2 + Math.random() * 0.6,
      size: 1 + Math.random() * 1.5,
      brightness: 0.3 + Math.random() * 0.7,
    })
  }
  return packets
}

// ===== MAIN EXPORT =====
export function drawLiveChart(ctx, w, h, timestamp, candles, featureValues, signal, tickerPrice, candles15m, liquidations) {
  ctx.clearRect(0, 0, w, h)
  if (!candles || candles.length < 2) return

  const progress = (timestamp % DURATION) / DURATION
  const { name: phase, local: t } = getPhase(progress)
  const globalTime = timestamp / 1000

  // Layout: chart left, analysis right
  const chartW = Math.floor(w * CHART_RATIO)
  const analysisW = w - chartW
  const analysisCx = chartW + analysisW / 2
  const analysisCy = h / 2

  // Compute ATR from candle history for live candle scaling
  const atr = computeATR(candles)

  // Compute 15m bias for wind effect (always from 15m chart)
  let bias15m = 0
  if (candles15m && candles15m.length > 2) {
    const last15 = candles15m[candles15m.length - 1]
    const ema21 = last15.ema21 || last15.close
    const ema50 = last15.ema50 || last15.close
    const range15 = candles15m[candles15m.length - 1].high - candles15m[candles15m.length - 1].low || 1
    const priceVsEma = (last15.close - ema21) / (range15 * 3 || 1)
    const emaSlope = (ema21 - ema50) / (range15 * 2 || 1)
    bias15m = Math.max(-1, Math.min(1, priceVsEma * 0.6 + emaSlope * 0.4))
  }

  // Draw chart section (returns last candle screen position)
  const lastPos = drawChartSection(ctx, chartW, h, candles, tickerPrice, bias15m, liquidations)

  // Draw data bridge from chart to analysis zone
  if (lastPos) {
    drawBridge(ctx, lastPos.x, lastPos.y, analysisCx, analysisCy, phase, t, globalTime)
  }

  // Draw live candle analysis with radial feature nodes
  drawAnalysisSection(ctx, analysisCx, analysisCy, analysisW, h, timestamp, phase, t, globalTime, candles, featureValues, signal, atr, tickerPrice)
}

// ===== CHART SECTION =====
function drawChartSection(ctx, chartW, h, candles, tickerPrice, bias15m, liquidations) {
  const data = candles.slice(-50)

  // Patch last candle with live ticker price
  if (tickerPrice && data.length > 0) {
    const last = { ...data[data.length - 1] }
    last.close = tickerPrice
    last.high = Math.max(last.high, tickerPrice)
    last.low = Math.min(last.low, tickerPrice)
    data[data.length - 1] = last
  }
  const pad = { top: 20, bottom: 50, left: 10, right: 25 }
  const innerW = chartW - pad.left - pad.right
  const innerH = h - pad.top - pad.bottom
  const volH = 40
  const priceH = innerH - volH

  let minP = Infinity, maxP = -Infinity, maxVol = 0
  for (const c of data) {
    minP = Math.min(minP, c.low)
    maxP = Math.max(maxP, c.high)
    maxVol = Math.max(maxVol, c.volume || 0)
  }
  const priceRange = maxP - minP || 1
  const candleWidth = innerW / data.length
  const bodyW = Math.max(1, candleWidth * 0.6)

  function priceY(p) {
    return pad.top + (1 - (p - minP) / priceRange) * priceH
  }

  // Clip to chart area
  ctx.save()
  ctx.beginPath()
  ctx.rect(0, 0, chartW, h)
  ctx.clip()

  // Price grid
  ctx.strokeStyle = 'rgba(255,255,255,0.03)'
  ctx.lineWidth = 0.5
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (priceH / 4) * i
    ctx.beginPath()
    ctx.moveTo(pad.left, y)
    ctx.lineTo(chartW - pad.right, y)
    ctx.stroke()
    const p = maxP - (priceRange / 4) * i
    ctx.fillStyle = 'rgba(255,255,255,0.12)'
    ctx.font = '9px JetBrains Mono, monospace'
    ctx.textAlign = 'left'
    ctx.fillText('$' + Math.round(p).toLocaleString(), pad.left + 2, y - 3)
  }

  // EMA cyber-ribbons — glowing energy waves the candles swim in
  function getEmaPoints(field) {
    const pts = []
    for (let i = 0; i < data.length; i++) {
      const val = data[i][field]
      if (!val) continue
      pts.push({ x: pad.left + i * candleWidth + candleWidth / 2, y: priceY(val) })
    }
    return pts
  }
  const ema21pts = getEmaPoints('ema21')
  const ema50pts = getEmaPoints('ema50')

  // Fill between EMAs — the "river" candles swim in
  if (ema21pts.length > 1 && ema50pts.length > 1) {
    ctx.save()
    ctx.beginPath()
    for (let i = 0; i < ema21pts.length; i++) {
      const p = ema21pts[i]
      if (i === 0) ctx.moveTo(p.x, p.y)
      else ctx.lineTo(p.x, p.y)
    }
    for (let i = ema50pts.length - 1; i >= 0; i--) {
      ctx.lineTo(ema50pts[i].x, ema50pts[i].y)
    }
    ctx.closePath()
    const ribbonGrad = ctx.createLinearGradient(0, pad.top, 0, pad.top + priceH)
    ribbonGrad.addColorStop(0, 'rgba(0, 240, 255, 0.04)')
    ribbonGrad.addColorStop(0.5, 'rgba(160, 0, 255, 0.06)')
    ribbonGrad.addColorStop(1, 'rgba(255, 0, 255, 0.03)')
    ctx.fillStyle = ribbonGrad
    ctx.fill()
    ctx.restore()
  }

  // Draw each EMA as glowing ribbon
  function drawEmaRibbon(pts, color, width) {
    if (pts.length < 2) return
    const rgb = hexToRgb(color)

    // Outer glow
    ctx.save()
    ctx.beginPath()
    for (let i = 0; i < pts.length; i++) {
      if (i === 0) ctx.moveTo(pts[i].x, pts[i].y)
      else ctx.lineTo(pts[i].x, pts[i].y)
    }
    ctx.strokeStyle = `rgba(${rgb}, 0.08)`
    ctx.lineWidth = width + 6
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.stroke()

    // Mid glow
    ctx.strokeStyle = `rgba(${rgb}, 0.2)`
    ctx.lineWidth = width + 2
    ctx.stroke()

    // Core line
    ctx.strokeStyle = `rgba(${rgb}, 0.6)`
    ctx.lineWidth = width
    ctx.shadowColor = color
    ctx.shadowBlur = 8
    ctx.stroke()
    ctx.shadowBlur = 0

    // Flowing energy dots along the EMA
    const dotCount = 6
    const time = Date.now() / 1000
    for (let d = 0; d < dotCount; d++) {
      const t = ((time * 0.3 + d / dotCount) % 1)
      const idx = t * (pts.length - 1)
      const i0 = Math.floor(idx)
      const i1 = Math.min(i0 + 1, pts.length - 1)
      const frac = idx - i0
      const dx = pts[i0].x + (pts[i1].x - pts[i0].x) * frac
      const dy = pts[i0].y + (pts[i1].y - pts[i0].y) * frac

      ctx.fillStyle = `rgba(${rgb}, ${0.4 + Math.sin(time * 4 + d * 2) * 0.3})`
      ctx.shadowColor = color
      ctx.shadowBlur = 6
      ctx.beginPath()
      ctx.arc(dx, dy, 1.5, 0, Math.PI * 2)
      ctx.fill()
      ctx.shadowBlur = 0
    }

    ctx.restore()
  }
  drawEmaRibbon(ema21pts, '#00f0ff', 1.5)
  drawEmaRibbon(ema50pts, '#ff00ff', 1.5)

  // Cyber dust — particles raining down from EMAs
  for (const [pts, color] of [[ema21pts, '#00f0ff'], [ema50pts, '#ff00ff']]) {
    if (pts.length < 2) continue
    const spawnCount = Math.random() > 0.4 ? 2 : 1
    for (let s = 0; s < spawnCount; s++) {
      if (_emaDust.length >= MAX_DUST) break
      const idx = Math.floor(Math.random() * (pts.length - 1))
      const frac = Math.random()
      const p0 = pts[idx]
      const p1 = pts[idx + 1] || p0
      _emaDust.push({
        x: p0.x + (p1.x - p0.x) * frac,
        y: p0.y + (p1.y - p0.y) * frac,
        vx: (Math.random() - 0.5) * 0.3,
        vy: 0.2 + Math.random() * 0.6,
        life: 1,
        decay: 0.006 + Math.random() * 0.014,
        size: 0.5 + Math.random() * 1.5,
        color,
      })
    }
  }
  ctx.save()
  for (let i = _emaDust.length - 1; i >= 0; i--) {
    const p = _emaDust[i]
    p.x += p.vx
    p.y += p.vy
    p.vx += (Math.random() - 0.5) * 0.04
    p.life -= p.decay
    if (p.life <= 0) { _emaDust.splice(i, 1); continue }
    const rgb = hexToRgb(p.color)
    const alpha = p.life * 0.35
    ctx.fillStyle = `rgba(${rgb}, ${alpha})`
    ctx.shadowColor = p.color
    ctx.shadowBlur = p.size * 2
    ctx.beginPath()
    ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.shadowBlur = 0
  ctx.restore()

  // Volume — laser fire beams flickering upward
  const volBase = pad.top + priceH + volH
  const time = Date.now() / 1000
  ctx.save()
  for (let i = 0; i < data.length; i++) {
    const c = data[i]
    const x = pad.left + i * candleWidth + candleWidth / 2
    const vRatio = maxVol > 0 ? (c.volume || 0) / maxVol : 0
    if (vRatio < 0.01) continue
    const vH = vRatio * volH * 0.85
    const bull = c.close >= c.open
    const baseColor = bull ? '0, 255, 136' : '255, 51, 85'
    const coreColor = bull ? '#00ff88' : '#ff3355'

    // Flicker — each bar has its own phase from seed
    const seed = i * 7.13
    const flicker1 = Math.sin(time * 12 + seed) * 0.3 + 0.7
    const flicker2 = Math.sin(time * 18 + seed * 1.7) * 0.2 + 0.8
    const flickerH = vH * flicker1 * flicker2

    // Outer glow beam
    const beamW = bodyW + 2
    const glowGrad = ctx.createLinearGradient(x, volBase, x, volBase - flickerH - 6)
    glowGrad.addColorStop(0, `rgba(${baseColor}, ${0.15 * vRatio})`)
    glowGrad.addColorStop(0.4, `rgba(${baseColor}, ${0.08 * vRatio})`)
    glowGrad.addColorStop(1, `rgba(${baseColor}, 0)`)
    ctx.fillStyle = glowGrad
    ctx.fillRect(x - beamW / 2, volBase - flickerH - 6, beamW, flickerH + 6)

    // Core laser beam — thin bright center
    const coreW = Math.max(1, bodyW * 0.4)
    const coreGrad = ctx.createLinearGradient(x, volBase, x, volBase - flickerH)
    coreGrad.addColorStop(0, `rgba(${baseColor}, ${0.6 * flicker2})`)
    coreGrad.addColorStop(0.3, `rgba(${baseColor}, ${0.35 * flicker1})`)
    coreGrad.addColorStop(0.7, `rgba(${baseColor}, ${0.12 * flicker1})`)
    coreGrad.addColorStop(1, `rgba(${baseColor}, 0)`)
    ctx.fillStyle = coreGrad
    ctx.fillRect(x - coreW / 2, volBase - flickerH, coreW, flickerH)

    // Hot tip — bright spark at the top that dances
    const tipY = volBase - flickerH - Math.sin(time * 15 + seed) * 3
    const tipAlpha = 0.4 + Math.sin(time * 20 + seed * 2.3) * 0.3
    ctx.shadowColor = coreColor
    ctx.shadowBlur = 6
    ctx.fillStyle = `rgba(${baseColor}, ${tipAlpha})`
    ctx.beginPath()
    ctx.arc(x, tipY, 1.2 + vRatio * 1.5, 0, Math.PI * 2)
    ctx.fill()
    ctx.shadowBlur = 0

    // Ember sparks — tiny particles drifting up from high-volume bars
    if (vRatio > 0.3) {
      const sparkCount = Math.floor(vRatio * 3)
      for (let s = 0; s < sparkCount; s++) {
        const sparkPhase = (time * 4 + seed + s * 2.7) % 1
        const sparkX = x + (Math.sin(time * 8 + s * 5.3 + seed) * bodyW * 0.6)
        const sparkY = tipY - sparkPhase * 12
        const sparkAlpha = (1 - sparkPhase) * 0.35
        ctx.fillStyle = `rgba(${baseColor}, ${sparkAlpha})`
        ctx.beginPath()
        ctx.arc(sparkX, sparkY, 0.5 + (1 - sparkPhase) * 0.8, 0, Math.PI * 2)
        ctx.fill()
      }
    }

    // Base glow line — hot ember at the bottom
    ctx.fillStyle = `rgba(${baseColor}, ${0.1 * flicker2})`
    ctx.fillRect(x - beamW / 2, volBase - 1, beamW, 1)
  }
  ctx.restore()

  // --- Bias wind — atmospheric mist from 15m chart ---
  {
    _biasSmooth += (bias15m - _biasSmooth) * 0.02
    const bias = _biasSmooth
    const absBias = Math.abs(bias)
    const isBull = bias > 0
    const fogColor = isBull ? '0, 255, 136' : '255, 51, 85'
    const windDir = isBull ? -1 : 1  // -1 = upward, +1 = downward

    // Spawn fog particles
    const spawnRate = absBias > 0.1 ? (absBias > 0.5 ? 3 : 2) : 1
    for (let s = 0; s < spawnRate; s++) {
      if (_biasFog.length >= MAX_FOG) break
      _biasFog.push({
        x: pad.left + Math.random() * innerW,
        y: isBull
          ? pad.top + priceH * 0.5 + Math.random() * priceH * 0.5  // spawn in lower half for upward
          : pad.top + Math.random() * priceH * 0.5,                 // spawn in upper half for downward
        vx: (Math.random() - 0.5) * 0.4,
        vy: windDir * (0.15 + Math.random() * 0.4) * absBias,
        life: 1,
        decay: 0.004 + Math.random() * 0.008,
        size: 8 + Math.random() * 20,
      })
    }

    // Update and draw fog
    ctx.save()
    for (let i = _biasFog.length - 1; i >= 0; i--) {
      const p = _biasFog[i]
      p.x += p.vx + (Math.random() - 0.5) * 0.2
      p.y += p.vy
      p.life -= p.decay
      if (p.life <= 0 || p.y < pad.top - 20 || p.y > pad.top + priceH + 20) {
        _biasFog.splice(i, 1)
        continue
      }
      const alpha = p.life * absBias * 0.06
      const grad = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * p.life)
      grad.addColorStop(0, `rgba(${fogColor}, ${alpha})`)
      grad.addColorStop(0.6, `rgba(${fogColor}, ${alpha * 0.3})`)
      grad.addColorStop(1, `rgba(${fogColor}, 0)`)
      ctx.fillStyle = grad
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2)
      ctx.fill()
    }
    ctx.restore()

  }

  // --- Whale liquidation zones — Skullwatcher data with magnetic dust ---
  {
    // Filter liquidation levels that are within the chart's visible price range
    const liqLevels = []
    if (liquidations && liquidations.length > 0) {
      for (const pred of liquidations) {
        if (pred.price >= minP && pred.price <= maxP) {
          liqLevels.push(pred)
        }
      }
    }

    // Draw zone bands at each liquidation level
    for (const lvl of liqLevels) {
      const y = priceY(lvl.price)
      const isUp = lvl.direction === 'UP'  // UP = short liquidations above price
      const zoneColor = isUp ? '255, 140, 0' : '0, 180, 255'

      // Zone thickness scales with USD value
      const usdK = (lvl.usd_value || 50000) / 1000
      const zoneH = Math.min(16, 4 + Math.log10(Math.max(1, usdK)) * 3)

      // Probability determines intensity
      const probAlpha = lvl.probability === 'VERY_HIGH' ? 0.10
        : lvl.probability === 'HIGH' ? 0.07 : 0.04

      // Soft gradient zone band
      const zGrad = ctx.createLinearGradient(pad.left, y - zoneH / 2, pad.left, y + zoneH / 2)
      zGrad.addColorStop(0, `rgba(${zoneColor}, 0)`)
      zGrad.addColorStop(0.5, `rgba(${zoneColor}, ${probAlpha})`)
      zGrad.addColorStop(1, `rgba(${zoneColor}, 0)`)
      ctx.fillStyle = zGrad
      ctx.fillRect(pad.left, y - zoneH / 2, innerW, zoneH)

      // Dashed level line
      ctx.save()
      ctx.setLineDash([2, 5])
      ctx.strokeStyle = `rgba(${zoneColor}, 0.15)`
      ctx.lineWidth = 0.5
      ctx.beginPath()
      ctx.moveTo(pad.left, y)
      ctx.lineTo(pad.left + innerW, y)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.restore()

      // Label — skull + USD value + leverage
      ctx.fillStyle = `rgba(${zoneColor}, 0.25)`
      ctx.font = '6px JetBrains Mono, monospace'
      ctx.textAlign = 'right'
      const usdLabel = usdK >= 1000 ? `$${(usdK / 1000).toFixed(1)}M` : `$${Math.round(usdK)}K`
      const leverageLabel = lvl.leverage ? `${lvl.leverage}x` : ''
      ctx.fillText(`☠ ${usdLabel} ${leverageLabel}`, pad.left + innerW - 2, y - 4)
    }

    // Spawn magnetic dust near liquidation levels
    if (liqLevels.length > 0 && _magnetDust.length < MAX_MAGNET) {
      const spawnCount = Math.min(2, MAX_MAGNET - _magnetDust.length)
      for (let s = 0; s < spawnCount; s++) {
        const lvl = liqLevels[Math.floor(Math.random() * liqLevels.length)]
        const targetY = priceY(lvl.price)
        const isUp = lvl.direction === 'UP'
        _magnetDust.push({
          x: pad.left + Math.random() * innerW,
          y: targetY + (Math.random() - 0.5) * 70,
          targetY,
          vx: (Math.random() - 0.5) * 0.4,
          vy: (Math.random() - 0.5) * 0.3,
          life: 1,
          decay: 0.003 + Math.random() * 0.007,
          size: 0.6 + Math.random() * 1.4,
          isUp,
        })
      }
    }

    // Update + draw magnetic dust particles
    ctx.save()
    for (let i = _magnetDust.length - 1; i >= 0; i--) {
      const p = _magnetDust[i]

      // Magnetic attraction toward target zone
      const dy = p.targetY - p.y
      const dist = Math.abs(dy)
      const force = 0.015 * Math.sign(dy) * Math.min(1, dist / 25)
      p.vy += force
      p.vy *= 0.95
      p.vx *= 0.98

      p.x += p.vx + (Math.random() - 0.5) * 0.12
      p.y += p.vy
      p.life -= p.decay

      if (p.x < pad.left - 10 || p.x > pad.left + innerW + 10) p.vx *= -0.5
      if (p.life <= 0) { _magnetDust.splice(i, 1); continue }

      const rgb = p.isUp ? '255, 140, 0' : '0, 180, 255'
      const nearZone = dist < 6
      const alpha = p.life * (nearZone ? 0.5 : 0.3)

      ctx.fillStyle = `rgba(${rgb}, ${alpha})`
      if (nearZone) {
        ctx.shadowColor = p.isUp ? '#ff8c00' : '#00b4ff'
        ctx.shadowBlur = p.size * 3
      }
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2)
      ctx.fill()
      ctx.shadowBlur = 0
    }
    ctx.restore()
  }

  // Candles — tech styled
  let lastPos = null
  for (let i = 0; i < data.length; i++) {
    const c = data[i]
    const x = pad.left + i * candleWidth + candleWidth / 2
    const bull = c.close >= c.open
    const color = bull ? '#00ff88' : '#ff3355'
    const rgb = hexToRgb(color)
    const isLast = i === data.length - 1

    const highY = priceY(c.high)
    const lowY = priceY(c.low)
    const bTop = priceY(Math.max(c.open, c.close))
    const bBot = priceY(Math.min(c.open, c.close))
    const bH = Math.max(1, bBot - bTop)

    // Wick — dashed with glow
    ctx.save()
    ctx.strokeStyle = `rgba(${rgb}, 0.5)`
    ctx.setLineDash([1, 2])
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, highY)
    ctx.lineTo(x, bTop)
    ctx.moveTo(x, bBot)
    ctx.lineTo(x, lowY)
    ctx.stroke()
    ctx.setLineDash([])

    // Wick endpoints — small diamonds at high/low
    for (const ey of [highY, lowY]) {
      ctx.fillStyle = `rgba(${rgb}, 0.4)`
      ctx.beginPath()
      ctx.moveTo(x, ey - 2)
      ctx.lineTo(x + 2, ey)
      ctx.lineTo(x, ey + 2)
      ctx.lineTo(x - 2, ey)
      ctx.closePath()
      ctx.fill()
    }

    // Body — gradient fill with border
    const bodyGrad = ctx.createLinearGradient(x, bTop, x, bBot + 1)
    bodyGrad.addColorStop(0, `rgba(${rgb}, ${bull ? 0.7 : 0.5})`)
    bodyGrad.addColorStop(1, `rgba(${rgb}, ${bull ? 0.4 : 0.25})`)
    ctx.fillStyle = bodyGrad
    ctx.fillRect(x - bodyW / 2, bTop, bodyW, bH)

    // Body border
    ctx.strokeStyle = `rgba(${rgb}, 0.6)`
    ctx.lineWidth = 0.5
    ctx.strokeRect(x - bodyW / 2, bTop, bodyW, bH)

    // Horizontal scan line through body
    if (bH > 4) {
      const scanY = bTop + (bH / 2)
      ctx.strokeStyle = `rgba(${rgb}, 0.15)`
      ctx.lineWidth = 0.5
      ctx.beginPath()
      ctx.moveTo(x - bodyW / 2 - 2, scanY)
      ctx.lineTo(x + bodyW / 2 + 2, scanY)
      ctx.stroke()
    }

    // Open/close tick marks on wick
    ctx.strokeStyle = `rgba(${rgb}, 0.35)`
    ctx.lineWidth = 0.5
    const tickW = bodyW * 0.3
    // open tick (left)
    const openY = priceY(c.open)
    ctx.beginPath()
    ctx.moveTo(x - tickW - 1, openY)
    ctx.lineTo(x - 1, openY)
    ctx.stroke()
    // close tick (right)
    const closeY = priceY(c.close)
    ctx.beginPath()
    ctx.moveTo(x + 1, closeY)
    ctx.lineTo(x + tickW + 1, closeY)
    ctx.stroke()

    ctx.restore()

    if (isLast) {
      // Glow pulse on last candle
      ctx.save()
      ctx.shadowColor = color
      ctx.shadowBlur = 15
      ctx.fillStyle = `rgba(${rgb}, 0.4)`
      ctx.fillRect(x - bodyW / 2, bTop, bodyW, bH)
      ctx.shadowBlur = 0
      ctx.restore()
      lastPos = { x, y: bTop + bH / 2, color }

      // "LIVE" label
      ctx.fillStyle = `rgba(${rgb}, 0.5)`
      ctx.font = '8px JetBrains Mono, monospace'
      ctx.textAlign = 'center'
      ctx.fillText('LIVE', x, bBot + 14)
    }
  }

  // Right edge fade (chart dissolves into analysis area)
  const fadeW = 30
  const fadeGrad = ctx.createLinearGradient(chartW - fadeW, 0, chartW, 0)
  fadeGrad.addColorStop(0, 'rgba(5, 5, 8, 0)')
  fadeGrad.addColorStop(1, 'rgba(5, 5, 8, 0.85)')
  ctx.fillStyle = fadeGrad
  ctx.fillRect(chartW - fadeW, 0, fadeW, h)

  ctx.restore()
  return lastPos
}

// ===== DATA BRIDGE =====
function drawBridge(ctx, fromX, fromY, toX, toY, phase, t, globalTime) {
  if (!_bridgePackets) _bridgePackets = initBridgePackets()

  const dx = toX - fromX
  const dy = toY - fromY

  const bridgeAlpha = phase === 'ANALYSIS_PULSE'
    ? 0.25 + Math.sin(t * Math.PI) * 0.2
    : 0.08

  ctx.save()

  // Dashed bridge line
  ctx.setLineDash([3, 6])
  ctx.strokeStyle = `rgba(0, 240, 255, ${bridgeAlpha})`
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(fromX, fromY)
  ctx.lineTo(toX, toY)
  ctx.stroke()
  ctx.setLineDash([])

  // Flowing data packets along bridge
  const dt = 0.016
  const speedMult = phase === 'ANALYSIS_PULSE' ? 2.0 : 0.8
  for (const pkt of _bridgePackets) {
    pkt.offset += dt * pkt.speed * speedMult
    if (pkt.offset > 1) pkt.offset -= 1

    const px = fromX + dx * pkt.offset
    const py = fromY + dy * pkt.offset
    const alpha = pkt.brightness * bridgeAlpha * 2.5
    if (alpha < 0.02) continue

    ctx.fillStyle = `rgba(0, 240, 255, ${alpha})`
    ctx.shadowColor = '#00f0ff'
    ctx.shadowBlur = pkt.size * 4
    ctx.beginPath()
    ctx.arc(px, py, pkt.size, 0, Math.PI * 2)
    ctx.fill()
    ctx.shadowBlur = 0
  }

  ctx.restore()
}

// ===== ANALYSIS SECTION (live candle + radial feature nodes) =====
function drawAnalysisSection(ctx, cx, cy, areaW, areaH, timestamp, phase, t, globalTime, candles, featureValues, signal, atr, tickerPrice) {
  if (!candles[candles.length - 1]) return

  // Lerped live candle — smoothly builds up from open when new candle appears
  const liveCandle = getLerpedCandle(candles, tickerPrice)

  const bull = liveCandle.close >= liveCandle.open
  const bodyColor = bull ? '#00ff88' : '#ff3355'
  const wickColor = bodyColor

  // Live candle dimensions — scaled relative to ATR so new candles start small
  const maxCandleH = 120
  const scale = maxCandleH / (atr * 1.5)
  const bodyTopPrice = Math.max(liveCandle.open, liveCandle.close)
  const bodyBotPrice = Math.min(liveCandle.open, liveCandle.close)
  const bodyH = Math.max(2, (bodyTopPrice - bodyBotPrice) * scale)
  const upperWickH = (liveCandle.high - bodyTopPrice) * scale
  const lowerWickH = (bodyBotPrice - liveCandle.low) * scale
  const totalRange = liveCandle.high - liveCandle.low
  const candleW = Math.max(12, 36 * Math.min(1, totalRange / atr + 0.3))
  const wickW = 2
  const candleTop = cy - bodyH / 2
  const candleBot = cy + bodyH / 2

  // Phase state
  let upperWickOffset = 0
  let lowerWickOffset = 0
  let bodyGlow = 0
  let showLines = false
  let lineAlpha = 0
  let particleProgress = 0
  let dataFlowActive = false

  switch (phase) {
    case 'ASSEMBLED':
      bodyGlow = 0.2 + Math.sin(t * Math.PI) * 0.1
      dataFlowActive = true
      lineAlpha = 0.08
      break
    case 'DISASSEMBLE':
      upperWickOffset = -easeOut(t) * 45
      lowerWickOffset = easeOut(t) * 45
      dataFlowActive = true
      lineAlpha = 0.05 + t * 0.15
      break
    case 'ANALYSIS_PULSE': {
      upperWickOffset = -45
      lowerWickOffset = 45
      showLines = true
      lineAlpha = 0.15 + Math.sin(t * Math.PI) * 0.55
      particleProgress = t
      bodyGlow = 0.1 + Math.sin(t * Math.PI * 3) * 0.15
      dataFlowActive = true
      break
    }
    case 'REASSEMBLE':
      upperWickOffset = -45 * (1 - easeInOut(t))
      lowerWickOffset = 45 * (1 - easeInOut(t))
      lineAlpha = Math.max(0.05, 0.7 * (1 - t))
      showLines = true
      dataFlowActive = true
      break
    case 'GLOW':
      bodyGlow = 0.3 + (1 - t) * 0.4
      dataFlowActive = true
      lineAlpha = 0.08 * (1 - t)
      break
  }

  // Radial nodes
  const radius = Math.min(areaW * 0.40, areaH * 0.36)
  const nodes = computeRadialPositions(cx, cy, radius)

  if (!_dataPackets || _dataPackets.length === 0) {
    _dataPackets = initDataPackets(nodes.length)
  }

  // --- Background: hex grid + concentric rings ---
  ctx.save()
  ctx.globalAlpha = 0.03
  ctx.strokeStyle = '#00f0ff'
  ctx.lineWidth = 0.5
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(a) * radius * 0.4, cy + Math.sin(a) * radius * 0.4)
    ctx.lineTo(cx + Math.cos(a) * radius * 1.05, cy + Math.sin(a) * radius * 1.05)
    ctx.stroke()
  }
  for (const r of [radius * 0.3, radius * 0.6, radius * 0.9]) {
    ctx.beginPath()
    ctx.arc(cx, cy, r, 0, Math.PI * 2)
    ctx.stroke()
  }
  ctx.restore()

  // --- Connection lines (candle center to each node) ---
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i]
    const rgb = hexToRgb(node.color)
    if (!showLines && lineAlpha <= 0.02) continue

    const delay = i / nodes.length
    let alpha = lineAlpha
    if (phase === 'ANALYSIS_PULSE') {
      alpha *= Math.min(1, Math.max(0, (particleProgress - delay * 0.5) * 3))
    }
    if (alpha <= 0.01) continue

    ctx.save()
    // Primary line — dashed circuit trace
    ctx.setLineDash([2, 4])
    ctx.strokeStyle = `rgba(${rgb}, ${alpha * 0.5})`
    ctx.lineWidth = 0.8
    ctx.beginPath()
    ctx.moveTo(cx, cy)
    ctx.lineTo(node.x, node.y)
    ctx.stroke()

    // Secondary line — solid offset for depth
    ctx.setLineDash([])
    ctx.strokeStyle = `rgba(${rgb}, ${alpha * 0.2})`
    ctx.lineWidth = 0.3
    const ox = Math.cos(node.angle + Math.PI / 2) * 3
    const oy = Math.sin(node.angle + Math.PI / 2) * 3
    ctx.beginPath()
    ctx.moveTo(cx + ox, cy + oy)
    ctx.lineTo(node.x + ox, node.y + oy)
    ctx.stroke()
    ctx.restore()
  }

  // --- Data packets flowing along lines ---
  if (dataFlowActive) {
    const dt = 0.016
    for (const pkt of _dataPackets) {
      const node = nodes[pkt.nodeIdx]
      if (!node) continue
      const rgb = hexToRgb(node.color)

      const speedMult = phase === 'ANALYSIS_PULSE' ? 2.5 : 0.6
      if (pkt.returning) {
        pkt.offset -= dt * pkt.speed * speedMult * 0.5
        if (pkt.offset < 0) { pkt.offset = 0; pkt.returning = false }
      } else {
        pkt.offset += dt * pkt.speed * speedMult
        if (pkt.offset > 1) { pkt.offset = 1; pkt.returning = true }
      }

      const px = cx + (node.x - cx) * pkt.offset
      const py = cy + (node.y - cy) * pkt.offset

      let pAlpha = pkt.brightness * lineAlpha * 2
      if (phase === 'ANALYSIS_PULSE') {
        pAlpha = pkt.brightness * (0.4 + Math.sin(t * Math.PI) * 0.5)
      }
      if (pAlpha < 0.02) continue

      ctx.save()

      // Motion trail
      const ddx = node.x - cx
      const ddy = node.y - cy
      const len = Math.sqrt(ddx * ddx + ddy * ddy)
      if (len < 1) { ctx.restore(); continue }
      const nx = ddx / len
      const ny = ddy / len
      const trailLen = pkt.speed * speedMult * 6
      const dir = pkt.returning ? 1 : -1

      const grad = ctx.createLinearGradient(
        px + nx * trailLen * dir, py + ny * trailLen * dir,
        px, py
      )
      grad.addColorStop(0, `rgba(${rgb}, 0)`)
      grad.addColorStop(1, `rgba(${rgb}, ${pAlpha * 0.6})`)
      ctx.strokeStyle = grad
      ctx.lineWidth = pkt.size * 0.6
      ctx.beginPath()
      ctx.moveTo(px + nx * trailLen * dir, py + ny * trailLen * dir)
      ctx.lineTo(px, py)
      ctx.stroke()

      // Core dot
      ctx.fillStyle = `rgba(${rgb}, ${pAlpha})`
      ctx.shadowColor = node.color
      ctx.shadowBlur = pkt.size * 3
      ctx.beginPath()
      ctx.arc(px, py, pkt.size, 0, Math.PI * 2)
      ctx.fill()
      ctx.shadowBlur = 0

      ctx.restore()
    }
  }

  // --- Group nodes (hexagonal tech-style) ---
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i]
    const rgb = hexToRgb(node.color)
    const activeCount = featureValues
      ? node.features.filter(f => featureValues[f] != null && featureValues[f] !== 0).length
      : 0
    const fillRatio = activeCount / node.features.length
    const isActive = phase === 'ANALYSIS_PULSE'
    const brightness = isActive ? 0.5 + fillRatio * 0.5 : 0.15 + fillRatio * 0.25

    const nodeR = 9
    const pulseR = isActive ? nodeR + Math.sin(globalTime * 4 + node.angle * 3) * 2 : nodeR

    ctx.save()

    // Corner bracket arcs
    ctx.strokeStyle = `rgba(${rgb}, ${brightness * 0.6})`
    ctx.lineWidth = 1.2
    for (let c = 0; c < 4; c++) {
      const startA = node.angle + (c / 4) * Math.PI * 2 + 0.15
      ctx.beginPath()
      ctx.arc(node.x, node.y, pulseR + 3, startA, startA + Math.PI * 0.35)
      ctx.stroke()
    }

    // Hexagonal fill
    ctx.fillStyle = `rgba(${rgb}, ${brightness * 0.12})`
    ctx.strokeStyle = `rgba(${rgb}, ${brightness * 0.35})`
    ctx.lineWidth = 0.7
    ctx.beginPath()
    for (let v = 0; v < 6; v++) {
      const a = (v / 6) * Math.PI * 2 - Math.PI / 6
      const hx = node.x + Math.cos(a) * nodeR
      const hy = node.y + Math.sin(a) * nodeR
      if (v === 0) ctx.moveTo(hx, hy)
      else ctx.lineTo(hx, hy)
    }
    ctx.closePath()
    ctx.fill()
    ctx.stroke()

    // Activity fill bar inside hex
    if (fillRatio > 0) {
      ctx.fillStyle = `rgba(${rgb}, ${brightness * 0.4})`
      const barH = nodeR * 2 * fillRatio
      const barW = nodeR * 1.1
      ctx.fillRect(node.x - barW / 2, node.y + nodeR - barH, barW, barH)
    }

    // Core dot
    ctx.fillStyle = `rgba(${rgb}, ${brightness})`
    if (isActive && brightness > 0.6) {
      ctx.shadowColor = node.color
      ctx.shadowBlur = 12
    }
    ctx.beginPath()
    ctx.arc(node.x, node.y, 2, 0, Math.PI * 2)
    ctx.fill()
    ctx.shadowBlur = 0

    ctx.restore()

    // Label
    ctx.fillStyle = `rgba(${rgb}, ${Math.max(0.25, brightness * 0.7)})`
    ctx.font = '7px JetBrains Mono, monospace'
    ctx.textAlign = 'center'
    ctx.fillText(node.name.toUpperCase(), node.x, node.y + pulseR + 13)

    // Feature count
    ctx.fillStyle = `rgba(${rgb}, ${brightness * 0.4})`
    ctx.font = '6px JetBrains Mono, monospace'
    ctx.fillText(`${activeCount}/${node.features.length}`, node.x, node.y + pulseR + 20)
  }

  // --- Scanner ring ---
  ctx.save()
  const scanAngle = globalTime * 1.5
  ctx.strokeStyle = `rgba(0, 240, 255, ${0.06 + (phase === 'ANALYSIS_PULSE' ? 0.12 : 0)})`
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.arc(cx, cy, 50, scanAngle, scanAngle + Math.PI * 0.4)
  ctx.stroke()
  ctx.beginPath()
  ctx.arc(cx, cy, 50, scanAngle + Math.PI, scanAngle + Math.PI * 1.4)
  ctx.stroke()

  ctx.strokeStyle = 'rgba(0, 240, 255, 0.04)'
  ctx.beginPath()
  ctx.arc(cx, cy, 44, 0, Math.PI * 2)
  ctx.stroke()
  ctx.restore()

  // --- Confidence gauge (arc around scanner ring) ---
  if (signal && signal.confidence != null) {
    _confidenceDisplay += (signal.confidence - _confidenceDisplay) * 0.05
    const conf = _confidenceDisplay

    const gaugeR = 62
    const startA = Math.PI * 0.8
    const endA = Math.PI * 2.2
    const totalA = endA - startA
    const confA = startA + totalA * Math.min(1, conf)

    let gaugeColor
    if (conf < 0.3) gaugeColor = '#ff3355'
    else if (conf < 0.6) gaugeColor = '#ffaa00'
    else gaugeColor = '#00ff88'

    ctx.save()
    // Background arc
    ctx.beginPath()
    ctx.arc(cx, cy, gaugeR, startA, endA)
    ctx.strokeStyle = 'rgba(255,255,255,0.05)'
    ctx.lineWidth = 4
    ctx.lineCap = 'round'
    ctx.stroke()

    // Value arc
    if (conf > 0.01) {
      ctx.beginPath()
      ctx.arc(cx, cy, gaugeR, startA, confA)
      ctx.strokeStyle = gaugeColor
      ctx.shadowColor = gaugeColor
      ctx.shadowBlur = 8
      ctx.lineWidth = 4
      ctx.lineCap = 'round'
      ctx.stroke()
      ctx.shadowBlur = 0
    }

    // Percentage + label
    ctx.fillStyle = gaugeColor
    ctx.font = '11px JetBrains Mono, monospace'
    ctx.textAlign = 'center'
    ctx.fillText(Math.round(conf * 100) + '%', cx, cy + gaugeR + 14)
    ctx.fillStyle = `rgba(${hexToRgb(gaugeColor)}, 0.4)`
    ctx.font = '6px JetBrains Mono, monospace'
    ctx.fillText('CONFIDENCE', cx, cy + gaugeR + 23)

    // Status text at top of arc gap
    ctx.fillStyle = `rgba(${hexToRgb(gaugeColor)}, 0.6)`
    ctx.font = '7px JetBrains Mono, monospace'
    ctx.fillText(signal.status || '', cx, cy - gaugeR - 6)

    ctx.restore()
  }

  // --- Live candle (with disassemble/reassemble animation) ---
  ctx.save()

  // Upper wick
  ctx.fillStyle = wickColor
  ctx.shadowColor = bodyColor
  ctx.shadowBlur = bodyGlow > 0.15 ? 8 : 0
  ctx.fillRect(
    cx - wickW / 2,
    candleTop - upperWickH + upperWickOffset,
    wickW,
    upperWickH
  )

  // Body
  ctx.fillStyle = bodyColor
  ctx.fillRect(cx - candleW / 2, candleTop, candleW, bodyH)

  // Lower wick
  ctx.fillRect(
    cx - wickW / 2,
    candleBot + lowerWickOffset,
    wickW,
    lowerWickH
  )
  ctx.shadowBlur = 0

  // Body glow
  if (bodyGlow > 0.15) {
    ctx.shadowColor = bodyColor
    ctx.shadowBlur = bodyGlow * 25
    ctx.fillStyle = `rgba(${hexToRgb(bodyColor)}, ${bodyGlow * 0.25})`
    ctx.fillRect(cx - candleW / 2 - 3, candleTop - 3, candleW + 6, bodyH + 6)
    ctx.shadowBlur = 0
  }

  // Binary data overlay on candle body
  if (phase === 'ANALYSIS_PULSE' || phase === 'DISASSEMBLE') {
    ctx.fillStyle = 'rgba(0, 0, 0, 0.3)'
    ctx.font = '6px JetBrains Mono, monospace'
    ctx.textAlign = 'center'
    const rows = Math.floor(bodyH / 10)
    for (let r = 0; r < rows; r++) {
      const by = candleTop + 8 + r * 10
      if (by > candleBot - 4) break
      const seed = Math.floor(globalTime * 3 + r * 7)
      let bits = ''
      for (let b = 0; b < 5; b++) {
        bits += ((seed + b * 13) % 3 === 0) ? '1' : '0'
      }
      ctx.fillText(bits, cx, by)
    }
  }

  ctx.restore()

  // --- Orbiting energy particles around live candle ---
  ctx.save()
  const orbCount = 8
  const orbR = 30 + (phase === 'ANALYSIS_PULSE' ? Math.sin(t * Math.PI) * 8 : 0)
  for (let i = 0; i < orbCount; i++) {
    const baseAngle = (i / orbCount) * Math.PI * 2
    const speed = 1.2 + (i % 3) * 0.4
    const a = baseAngle + globalTime * speed
    const r = orbR + Math.sin(globalTime * 3 + i * 1.7) * 6
    const px = cx + Math.cos(a) * r
    const py = cy + Math.sin(a) * r * 0.6  // elliptical orbit
    const sz = 1 + Math.sin(globalTime * 5 + i) * 0.5
    const alpha = phase === 'ANALYSIS_PULSE'
      ? 0.5 + Math.sin(t * Math.PI) * 0.3
      : 0.15 + Math.sin(globalTime * 2 + i) * 0.1

    ctx.fillStyle = `rgba(${hexToRgb(bodyColor)}, ${alpha})`
    ctx.shadowColor = bodyColor
    ctx.shadowBlur = sz * 4
    ctx.beginPath()
    ctx.arc(px, py, sz, 0, Math.PI * 2)
    ctx.fill()

    // Trail
    const prevA = a - 0.3
    const tpx = cx + Math.cos(prevA) * r
    const tpy = cy + Math.sin(prevA) * r * 0.6
    const tGrad = ctx.createLinearGradient(tpx, tpy, px, py)
    tGrad.addColorStop(0, `rgba(${hexToRgb(bodyColor)}, 0)`)
    tGrad.addColorStop(1, `rgba(${hexToRgb(bodyColor)}, ${alpha * 0.4})`)
    ctx.strokeStyle = tGrad
    ctx.lineWidth = sz * 0.5
    ctx.shadowBlur = 0
    ctx.beginPath()
    ctx.moveTo(tpx, tpy)
    ctx.lineTo(px, py)
    ctx.stroke()
  }
  ctx.restore()

  // --- Candle progress % — shows 1m candle lifecycle ---
  {
    const lastCandle = candles[candles.length - 1]
    if (lastCandle && lastCandle.time) {
      // Timestamps from API are in milliseconds
      const candleTimeMs = lastCandle.time > 1e12 ? lastCandle.time : lastCandle.time * 1000
      const elapsedMs = Date.now() - candleTimeMs
      const candleDuration = 60000  // 1 minute in ms
      const candlePct = Math.min(100, Math.max(0, (elapsedMs / candleDuration) * 100))

      // Thin progress arc inside scanner ring
      const progR = 38
      const progStartA = -Math.PI / 2
      const progEndA = progStartA + (Math.PI * 2) * (candlePct / 100)

      ctx.save()
      // Background ring
      ctx.beginPath()
      ctx.arc(cx, cy, progR, 0, Math.PI * 2)
      ctx.strokeStyle = 'rgba(255,255,255,0.04)'
      ctx.lineWidth = 2
      ctx.stroke()

      // Progress arc
      if (candlePct > 0.5) {
        const pColor = candlePct > 85 ? '#ff3355' : candlePct > 60 ? '#ffaa00' : '#00f0ff'
        ctx.beginPath()
        ctx.arc(cx, cy, progR, progStartA, progEndA)
        ctx.strokeStyle = pColor
        ctx.shadowColor = pColor
        ctx.shadowBlur = 4
        ctx.lineWidth = 2
        ctx.lineCap = 'round'
        ctx.stroke()
        ctx.shadowBlur = 0

        // Percentage text
        ctx.fillStyle = pColor
        ctx.font = '8px JetBrains Mono, monospace'
        ctx.textAlign = 'center'
        ctx.fillText(Math.floor(candlePct) + '%', cx, cy - progR - 6)
        ctx.fillStyle = `rgba(${hexToRgb(pColor)}, 0.35)`
        ctx.font = '5px JetBrains Mono, monospace'
        ctx.fillText('1M CANDLE', cx, cy - progR - 14)
      }
      ctx.restore()
    }
  }

  // --- Micro data readouts flanking the candle ---
  ctx.save()
  ctx.font = '6px JetBrains Mono, monospace'
  ctx.globalAlpha = 0.25 + (phase === 'ANALYSIS_PULSE' ? 0.2 : 0)

  // Left side — scrolling hex values
  const hexSeed = Math.floor(globalTime * 4)
  for (let r = 0; r < 5; r++) {
    const hy = cy - 20 + r * 9
    const val = ((hexSeed + r * 0x3F1) & 0xFFFF).toString(16).toUpperCase().padStart(4, '0')
    ctx.fillStyle = `rgba(${hexToRgb(bodyColor)}, 0.3)`
    ctx.textAlign = 'right'
    ctx.fillText('0x' + val, cx - candleW / 2 - 8, hy)
  }

  // Right side — cycling feature readouts
  const featureKeys = ['RSI', 'ATR', 'VOL', 'BOS', 'EMA']
  for (let r = 0; r < 5; r++) {
    const hy = cy - 20 + r * 9
    const vi = ((hexSeed + r * 7) % 99).toString().padStart(2, '0')
    ctx.fillStyle = `rgba(0, 240, 255, 0.3)`
    ctx.textAlign = 'left'
    ctx.fillText(featureKeys[r] + ':' + vi, cx + candleW / 2 + 8, hy)
  }

  ctx.restore()
}
