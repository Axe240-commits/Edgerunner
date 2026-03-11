import { useRef, useEffect } from 'react'
import { fmtFeatureValue } from '../../lib/formatters'
import './FeaturePanel.css'

export default function FeaturePanel({ features, status }) {
  const prevRef = useRef({})
  const listRef = useRef(null)

  useEffect(() => {
    if (!listRef.current) return
    const el = listRef.current
    if (el.scrollTop < el.scrollHeight - el.clientHeight) {
      el.scrollTop += 1
    } else {
      el.scrollTop = 0
    }
  }, [features])

  const computed = status?.computed || 115
  const ms = status?.processing_ms?.toFixed(1) || '0.0'

  return (
    <div className="panel" style={{ gridColumn: 3, gridRow: 2 }}>
      <div className="panel-title">Feature Engine</div>
      <div className="feature-progress">
        <span>{computed}/{computed} computed</span>
        <span>[{'█'.repeat(16)}] {ms}ms</span>
      </div>
      <div className="feature-list" ref={listRef}>
        {features && features.map((f) => {
          const newVal = fmtFeatureValue(f.value)
          const changed = prevRef.current[f.name] !== undefined && prevRef.current[f.name] !== newVal
          prevRef.current[f.name] = newVal
          return (
            <div className="feature-item" key={f.name}>
              <span className="feature-name">{f.name}</span>
              <span className={`feature-val${changed ? ' flash' : ''}`}>{newVal}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
