import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api/client'
import GROUPS from '../../lib/featureGroups'
import './FeatureTab.css'

function fmtVal(v) {
  if (v == null) return 'NULL'
  if (typeof v === 'string') return v.length > 30 ? v.slice(0, 30) + '...' : v
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v)
    return v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '.0')
  }
  return String(v)
}

function fmtTs(ts) {
  if (!ts) return '--'
  return new Date(ts).toLocaleString('sv-SE', { timeZone: 'Europe/Berlin' })
}

function valColor(name, v) {
  if (v == null) return 'var(--text-dim)'
  if (typeof v === 'number') {
    if (name.startsWith('bos_') || name === 'choch' || name.startsWith('is_') || name.endsWith('_kill') || name.endsWith('_div')) {
      return v > 0 ? 'var(--cyan)' : 'var(--text-dim)'
    }
    if (name.includes('bull') || name === 'is_bullish') return v > 0 ? 'var(--green)' : 'var(--text-dim)'
    if (name.includes('bear')) return v > 0 ? 'var(--red)' : 'var(--text-dim)'
  }
  return 'var(--text)'
}

/* ── Simple Context Chart ──────────────────────────────────── */
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

    const pad = { top: 10, bottom: 18, left: 6, right: 50 }
    const chartW = w - pad.left - pad.right
    const chartH = h - pad.top - pad.bottom
    const n = candles.length

    let pMin = Infinity, pMax = -Infinity
    for (const c of candles) { if (c.low < pMin) pMin = c.low; if (c.high > pMax) pMax = c.high }
    const pRange = pMax - pMin || 1
    pMin -= pRange * 0.04; pMax += pRange * 0.04
    const pSpan = pMax - pMin

    const toY = (p) => pad.top + chartH * (1 - (p - pMin) / pSpan)
    const gap = chartW / n
    const candleW = Math.max(3, Math.floor(gap * 0.65))
    const toX = (i) => pad.left + gap * i + gap / 2
    const centerIdx = candles.findIndex(c => c.timestamp === centerTs)

    // grid
    ctx.strokeStyle = 'rgba(0, 240, 255, 0.05)'
    ctx.lineWidth = 1
    for (let i = 0; i <= 4; i++) {
      const price = pMin + (pSpan * i / 4)
      const y = Math.round(toY(price)) + 0.5
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke()
      ctx.fillStyle = '#556677'
      ctx.font = '9px monospace'
      ctx.textAlign = 'left'
      ctx.fillText(price.toFixed(0), w - pad.right + 4, y + 3)
    }

    // candles
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

      // wick
      ctx.strokeStyle = isC ? color : (isBull ? 'rgba(0,255,136,0.5)' : 'rgba(255,51,85,0.5)')
      ctx.lineWidth = isC ? 2 : 1
      ctx.beginPath(); ctx.moveTo(x, toY(c.high)); ctx.lineTo(x, toY(c.low)); ctx.stroke()

      // body
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

      // event markers
      const ev = []
      if (c.bos_bull) ev.push({ l: 'B+', co: '#00ff88' })
      if (c.bos_bear) ev.push({ l: 'B-', co: '#ff3355' })
      if (c.is_seeker_kill) ev.push({ l: 'K', co: '#ff00ff' })
      if (c.bull_div) ev.push({ l: 'D+', co: '#44ffaa' })
      if (c.bear_div) ev.push({ l: 'D-', co: '#aa44ff' })
      if (ev.length) {
        ctx.font = 'bold 7px monospace'
        ctx.textAlign = 'center'
        ev.forEach((e, ei) => { ctx.fillStyle = e.co; ctx.fillText(e.l, x, h - pad.bottom + 10 + ei * 8) })
      }

      // center label
      if (isC) {
        ctx.fillStyle = '#00f0ff'
        ctx.font = 'bold 8px monospace'
        ctx.textAlign = 'center'
        ctx.fillText(fmtTs(c.timestamp).slice(5), x, pad.top - 2)
      }
    }
  }, [candles, centerTs])

  return (
    <div className="ftab-chart-wrap">
      <canvas ref={canvasRef} className="ftab-chart-canvas" />
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
  const [tsInput, setTsInput] = useState('')

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

  // Auto-refresh latest page so Feature Browser doesn't appear "stuck"
  useEffect(() => {
    const id = setInterval(() => {
      if (page === 1) loadPage(1)
    }, 5000)
    return () => clearInterval(id)
  }, [page, loadPage])

  const loadCandle = useCallback(async (ts) => {
    const res = await api(`/api/db/candle/${ts}/neighbors?tf=${tf}&range=12`)
    if (res?.center) {
      setCandle(res.center)
      setSelectedTs(ts)
      const seq = [...(res.before || []), res.center, ...(res.after || [])]
      setNeighbors(seq)
    }
  }, [tf])

  const handleGo = () => { if (tsInput) loadCandle(+tsInput) }
  const handleLatest = () => { if (data?.length > 0) loadCandle(data[0].timestamp) }

  const handlePrev = () => {
    if (!candle || !data) return
    const idx = data.findIndex(c => c.timestamp === candle.timestamp)
    if (idx >= 0 && idx < data.length - 1) loadCandle(data[idx + 1].timestamp)
    else if (page < pages) loadPage(page + 1)
  }

  const handleNext = () => {
    if (!candle || !data) return
    const idx = data.findIndex(c => c.timestamp === candle.timestamp)
    if (idx > 0) loadCandle(data[idx - 1].timestamp)
    else if (page > 1) loadPage(page - 1)
  }

  return (
    <div className="feature-tab panel">
      <div className="ftab-nav">
        <h2>FEATURE BROWSER ({tf})</h2>
        <div className="ftab-controls">
          <button className="stab-btn stab-btn--ghost" onClick={handleLatest}>Latest</button>
          <button className="stab-btn stab-btn--ghost" onClick={handlePrev}>Prev</button>
          <button className="stab-btn stab-btn--ghost" onClick={handleNext}>Next</button>
          <input className="ftab-ts-input" type="text" placeholder="Timestamp"
            value={tsInput} onChange={e => setTsInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleGo()} />
          <button className="stab-btn stab-btn--primary" onClick={handleGo}>Go</button>
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
                <th>Timestamp</th>
                <th>Close</th>
                <th>Volume</th>
                <th>Events</th>
              </tr>
            </thead>
            <tbody>
              {data?.map(c => {
                const events = []
                if (c.bos_bull) events.push('BOS+')
                if (c.bos_bear) events.push('BOS-')
                if (c.is_seeker_kill) events.push('KILL')
                if (c.bull_div) events.push('DIV+')
                if (c.bear_div) events.push('DIV-')
                if (c.choch) events.push('CHoCH')
                return (
                  <tr key={c.timestamp}
                    className={selectedTs === c.timestamp ? 'selected' : ''}
                    onClick={() => loadCandle(c.timestamp)}>
                    <td>{fmtTs(c.timestamp)}</td>
                    <td>{c.close?.toFixed(2)}</td>
                    <td>{c.volume?.toFixed(0)}</td>
                    <td className="ftab-events">{events.join(' ') || '-'}</td>
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
                  <span className="ftab-cs-ts">{fmtTs(candle.timestamp)}</span>
                  <span className={candle.is_bullish ? 'up' : 'down'}>
                    O:{candle.open?.toFixed(1)} H:{candle.high?.toFixed(1)} L:{candle.low?.toFixed(1)} C:{candle.close?.toFixed(1)}
                  </span>
                  <span className="ftab-cs-vol">Vol: {candle.volume?.toFixed(0)}</span>
                  {onUseAsPattern && (
                    <button className="stab-btn stab-btn--accent ftab-pattern-btn"
                      onClick={() => onUseAsPattern(candle.timestamp)}>
                      USE AS PATTERN
                    </button>
                  )}
                </div>
                {GROUPS.map(group => (
                  <div className="ftab-group" key={group.id}>
                    <h4 style={{ color: group.color }}>{group.name}</h4>
                    <div className="ftab-group-grid">
                      {group.features.map(fname => {
                        const v = candle[fname]
                        return (
                          <div className="ftab-feat" key={fname}>
                            <span className="ftab-feat-name">{fname}</span>
                            <span className="ftab-feat-val" style={{ color: valColor(fname, v) }}>
                              {fmtVal(v)}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </>
            ) : (
              <div className="ftab-empty">Select a candle to view features</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
