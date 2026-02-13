import { useRef, useEffect } from 'react'
import './SignalPanel.css'

function describeArc(cx, cy, r, startAngle, endAngle) {
  const start = { x: cx + r * Math.cos(startAngle), y: cy + r * Math.sin(startAngle) }
  const end = { x: cx + r * Math.cos(endAngle), y: cy + r * Math.sin(endAngle) }
  const largeArc = endAngle - startAngle > Math.PI ? 1 : 0
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 1 ${end.x} ${end.y}`
}

export default function SignalPanel({ signal }) {
  const valRef = useRef(0)
  const svgRef = useRef(null)
  const valueRef = useRef(null)
  const rafRef = useRef(null)

  useEffect(() => {
    let running = true
    const target = signal?.confidence || 0

    function animate() {
      if (!running) return
      valRef.current += (target - valRef.current) * 0.08
      const val = valRef.current
      const pct = Math.round(val * 100)

      const cx = 100, cy = 110, r = 80
      const startAngle = Math.PI * 0.8
      const endAngle = Math.PI * 2.2
      const totalAngle = endAngle - startAngle
      const valAngle = startAngle + totalAngle * val

      let color
      if (val < 0.3) color = '#ff3355'
      else if (val < 0.6) color = '#ffaa00'
      else color = '#00ff88'

      const bgD = describeArc(cx, cy, r, startAngle, endAngle)
      const valD = describeArc(cx, cy, r, startAngle, valAngle)

      if (svgRef.current) {
        svgRef.current.innerHTML = `
          <path d="${bgD}" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="8" stroke-linecap="round"/>
          <path d="${valD}" fill="none" stroke="${color}" stroke-width="8" stroke-linecap="round"
            style="filter: drop-shadow(0 0 6px ${color})"/>
          <circle cx="${cx}" cy="${cy}" r="2" fill="${color}" opacity="0.6"/>
        `
      }
      if (valueRef.current) {
        valueRef.current.textContent = pct + '%'
        valueRef.current.style.color = color
      }

      rafRef.current = requestAnimationFrame(animate)
    }

    rafRef.current = requestAnimationFrame(animate)
    return () => { running = false; cancelAnimationFrame(rafRef.current) }
  }, [signal])

  return (
    <div className="panel signal-panel" style={{ gridColumn: 2, gridRow: 3 }}>
      <div className="panel-title">Signal Confidence</div>
      <div className="gauge-container">
        <svg viewBox="0 0 200 200" ref={svgRef} />
        <div className="gauge-value" ref={valueRef}>0%</div>
      </div>
      <div className="gauge-status">{signal?.status || 'ANALYZING'}</div>
      <div className="gauge-label">{signal?.features_aligned || 0}/89 features aligned</div>
    </div>
  )
}
