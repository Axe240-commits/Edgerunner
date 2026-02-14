import { useRef, useEffect } from 'react'

/**
 * OutcomeOverlay — Canvas-based visualization overlaying ALL match outcomes.
 *
 * Props:
 *   matches: Array of match objects with `after[]` (forward candles)
 *   direction: 'LONG' | 'SHORT' | null — filters color
 */
export default function OutcomeOverlay({ matches }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !matches?.length) return

    const ctx = canvas.getContext('2d')
    const dpr = window.devicePixelRatio || 1
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    canvas.width = w * dpr
    canvas.height = h * dpr
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)

    // Compute PnL paths for each match
    const paths = []
    let maxLen = 0

    for (const m of matches) {
      const after = m.after
      if (!after?.length) continue
      const entry = m.meta?.close
      if (!entry) continue

      const pnl = after.map(c => ((c.close - entry) / entry) * 100)
      paths.push({ pnl, direction: m.outcome?.direction || 'LONG' })
      maxLen = Math.max(maxLen, pnl.length)
    }

    if (paths.length === 0 || maxLen === 0) {
      ctx.fillStyle = 'rgba(255,255,255,0.2)'
      ctx.font = '11px monospace'
      ctx.textAlign = 'center'
      ctx.fillText('No forward data — enable include_forward', w / 2, h / 2)
      return
    }

    // Compute avg and std
    const avg = []
    const std = []
    for (let i = 0; i < maxLen; i++) {
      const vals = paths.map(p => p.pnl[i]).filter(v => v !== undefined)
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length
      avg.push(mean)
      if (vals.length > 1) {
        const variance = vals.reduce((s, v) => s + (v - mean) ** 2, 0) / (vals.length - 1)
        std.push(Math.sqrt(variance))
      } else {
        std.push(0)
      }
    }

    // Find Y range
    let yMin = Infinity, yMax = -Infinity
    for (const p of paths) {
      for (const v of p.pnl) {
        yMin = Math.min(yMin, v)
        yMax = Math.max(yMax, v)
      }
    }
    // Include avg ± std
    for (let i = 0; i < maxLen; i++) {
      yMin = Math.min(yMin, avg[i] - std[i])
      yMax = Math.max(yMax, avg[i] + std[i])
    }

    const yPad = (yMax - yMin) * 0.1 || 0.1
    yMin -= yPad
    yMax += yPad

    // Chart area
    const padL = 50, padR = 16, padT = 24, padB = 28
    const cw = w - padL - padR
    const ch = h - padT - padB

    const toX = (i) => padL + (i / (maxLen - 1 || 1)) * cw
    const toY = (v) => padT + ch * (1 - (v - yMin) / (yMax - yMin))

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,0.06)'
    ctx.lineWidth = 1
    const ySteps = 5
    for (let i = 0; i <= ySteps; i++) {
      const v = yMin + (yMax - yMin) * (i / ySteps)
      const y = toY(v)
      ctx.beginPath()
      ctx.moveTo(padL, y)
      ctx.lineTo(w - padR, y)
      ctx.stroke()

      ctx.fillStyle = 'rgba(255,255,255,0.3)'
      ctx.font = '9px monospace'
      ctx.textAlign = 'right'
      ctx.fillText(`${v.toFixed(2)}%`, padL - 4, y + 3)
    }

    // X axis labels
    ctx.textAlign = 'center'
    ctx.fillStyle = 'rgba(255,255,255,0.3)'
    const xStep = Math.max(1, Math.floor(maxLen / 5))
    for (let i = 0; i < maxLen; i += xStep) {
      ctx.fillText(String(i + 1), toX(i), h - 8)
    }

    // Zero line
    if (yMin < 0 && yMax > 0) {
      ctx.strokeStyle = 'rgba(255,255,255,0.2)'
      ctx.setLineDash([4, 4])
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(padL, toY(0))
      ctx.lineTo(w - padR, toY(0))
      ctx.stroke()
      ctx.setLineDash([])
    }

    // Std band
    ctx.fillStyle = 'rgba(0, 240, 255, 0.06)'
    ctx.beginPath()
    for (let i = 0; i < maxLen; i++) {
      const x = toX(i)
      const y = toY(avg[i] + std[i])
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    }
    for (let i = maxLen - 1; i >= 0; i--) {
      const x = toX(i)
      const y = toY(avg[i] - std[i])
      ctx.lineTo(x, y)
    }
    ctx.closePath()
    ctx.fill()

    // Individual paths
    for (const p of paths) {
      const isLong = p.direction === 'LONG'
      ctx.strokeStyle = isLong ? 'rgba(0, 255, 136, 0.12)' : 'rgba(255, 51, 85, 0.12)'
      ctx.lineWidth = 1
      ctx.beginPath()
      for (let i = 0; i < p.pnl.length; i++) {
        const x = toX(i)
        const y = toY(p.pnl[i])
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
      }
      ctx.stroke()
    }

    // Avg line (bold cyan)
    ctx.strokeStyle = '#00f0ff'
    ctx.lineWidth = 2.5
    ctx.beginPath()
    for (let i = 0; i < maxLen; i++) {
      const x = toX(i)
      const y = toY(avg[i])
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    }
    ctx.stroke()

    // Legend
    ctx.font = '9px monospace'
    ctx.textAlign = 'left'
    const ly = 12
    ctx.fillStyle = '#00f0ff'
    ctx.fillText(`AVG (${paths.length} matches)`, padL + 4, ly)
    ctx.fillStyle = 'rgba(0,255,136,0.5)'
    ctx.fillText('LONG', padL + 160, ly)
    ctx.fillStyle = 'rgba(255,51,85,0.5)'
    ctx.fillText('SHORT', padL + 200, ly)
    ctx.fillStyle = 'rgba(0,240,255,0.3)'
    ctx.fillText('±1σ band', padL + 260, ly)

    // X-axis label
    ctx.fillStyle = 'rgba(255,255,255,0.3)'
    ctx.textAlign = 'center'
    ctx.fillText('Candles after entry', w / 2, h - 0)

  }, [matches])

  return (
    <div className="pm-overlay-container">
      <div className="pm-overlay-title">OUTCOME OVERLAY</div>
      <canvas ref={canvasRef} className="pm-overlay-canvas" />
    </div>
  )
}
