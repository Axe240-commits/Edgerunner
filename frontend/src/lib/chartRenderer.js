// Canvas Candlestick Chart Renderer
// Ported from edgerunner_dashboard.html lines 915-1066

export function drawCandlestickChart(ctx, w, h, candles, swingHighs, swingLows) {
  ctx.clearRect(0, 0, w, h)
  if (!candles || candles.length < 2) return

  const data = candles.slice(-60)
  const padding = { top: 10, bottom: 40, left: 5, right: 5 }
  const chartW = w - padding.left - padding.right
  const chartH = h - padding.top - padding.bottom
  const volH = 40
  const priceH = chartH - volH

  let minP = Infinity, maxP = -Infinity, maxVol = 0
  for (const c of data) {
    minP = Math.min(minP, c.low)
    maxP = Math.max(maxP, c.high)
    maxVol = Math.max(maxVol, c.volume)
  }
  const priceRange = maxP - minP || 1

  const candleW = chartW / data.length
  const bodyW = Math.max(1, candleW * 0.65)

  function priceY(p) {
    return padding.top + (1 - (p - minP) / priceRange) * priceH
  }

  // Grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.03)'
  ctx.lineWidth = 0.5
  for (let i = 0; i < 5; i++) {
    const y = padding.top + (priceH / 4) * i
    ctx.beginPath()
    ctx.moveTo(padding.left, y)
    ctx.lineTo(w - padding.right, y)
    ctx.stroke()
    const p = maxP - (priceRange / 4) * i
    ctx.fillStyle = 'rgba(255,255,255,0.15)'
    ctx.font = '9px JetBrains Mono, Courier New'
    ctx.fillText('$' + Math.round(p).toLocaleString(), padding.left + 2, y - 2)
  }

  // EMA lines
  function drawEmaLine(field, color) {
    ctx.beginPath()
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.globalAlpha = 0.5
    for (let i = 0; i < data.length; i++) {
      const x = padding.left + i * candleW + candleW / 2
      const y = priceY(data[i][field] || data[i].close)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
    ctx.globalAlpha = 1
  }
  drawEmaLine('ema21', '#00f0ff')
  drawEmaLine('ema50', '#ff00ff')

  // Volume bars
  const volBase = padding.top + priceH + volH
  for (let i = 0; i < data.length; i++) {
    const c = data[i]
    const x = padding.left + i * candleW + (candleW - bodyW) / 2
    const vH = (c.volume / maxVol) * volH * 0.8
    const bullish = c.close >= c.open
    ctx.fillStyle = bullish ? 'rgba(0, 255, 136, 0.15)' : 'rgba(255, 51, 85, 0.15)'
    ctx.fillRect(x, volBase - vH, bodyW, vH)
  }

  // Candles
  for (let i = 0; i < data.length; i++) {
    const c = data[i]
    const x = padding.left + i * candleW + candleW / 2
    const bullish = c.close >= c.open
    const color = bullish ? '#00ff88' : '#ff3355'

    // Wick
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, priceY(c.high))
    ctx.lineTo(x, priceY(c.low))
    ctx.stroke()

    // Body
    const bodyTop = priceY(Math.max(c.open, c.close))
    const bodyBot = priceY(Math.min(c.open, c.close))
    const bodyHeight = Math.max(1, bodyBot - bodyTop)
    ctx.fillStyle = color
    ctx.fillRect(x - bodyW / 2, bodyTop, bodyW, bodyHeight)

    // Last candle glow
    if (i === data.length - 1) {
      ctx.shadowColor = color
      ctx.shadowBlur = 8
      ctx.fillRect(x - bodyW / 2, bodyTop, bodyW, bodyHeight)
      ctx.shadowBlur = 0
    }
  }

  // Swing markers
  const offset = candles.length - 60
  if (swingHighs) {
    for (const sh of swingHighs) {
      const idx = sh.index - offset
      if (idx >= 0 && idx < 60) {
        const x = padding.left + idx * candleW + candleW / 2
        const y = priceY(sh.price) - 6
        ctx.fillStyle = '#ff335580'
        ctx.beginPath()
        ctx.moveTo(x, y); ctx.lineTo(x - 4, y - 6); ctx.lineTo(x + 4, y - 6)
        ctx.closePath()
        ctx.fill()
      }
    }
  }
  if (swingLows) {
    for (const sl of swingLows) {
      const idx = sl.index - offset
      if (idx >= 0 && idx < 60) {
        const x = padding.left + idx * candleW + candleW / 2
        const y = priceY(sl.price) + 6
        ctx.fillStyle = '#00ff8880'
        ctx.beginPath()
        ctx.moveTo(x, y); ctx.lineTo(x - 4, y + 6); ctx.lineTo(x + 4, y + 6)
        ctx.closePath()
        ctx.fill()
      }
    }
  }
}

// Sparkline renderer
export function drawSparkline(ctx, w, h, data) {
  ctx.clearRect(0, 0, w, h)
  if (!data || data.length < 2) return

  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1

  ctx.beginPath()
  ctx.strokeStyle = data[data.length - 1] >= data[0] ? '#00ff88' : '#ff3355'
  ctx.lineWidth = 1.5

  for (let i = 0; i < data.length; i++) {
    const x = (i / (data.length - 1)) * w
    const y = h - ((data[i] - min) / range) * (h - 4) - 2
    if (i === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  }
  ctx.stroke()

  // Gradient fill
  ctx.lineTo(w, h)
  ctx.lineTo(0, h)
  ctx.closePath()
  const grad = ctx.createLinearGradient(0, 0, 0, h)
  const col = data[data.length - 1] >= data[0] ? '0, 255, 136' : '255, 51, 85'
  grad.addColorStop(0, `rgba(${col}, 0.15)`)
  grad.addColorStop(1, `rgba(${col}, 0)`)
  ctx.fillStyle = grad
  ctx.fill()
}
