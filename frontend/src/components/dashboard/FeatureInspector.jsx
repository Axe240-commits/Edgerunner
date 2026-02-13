import { useState, useCallback } from 'react'
import { api } from '../../api/client'
import GROUPS from '../../lib/featureGroups'
import { fmtFeatureValue } from '../../lib/formatters'
import Panel from '../ui/Panel'
import './FeatureInspector.css'

export default function FeatureInspector({ tf }) {
  const [timestamp, setTimestamp] = useState('')
  const [candle, setCandle] = useState(null)
  const [error, setError] = useState('')

  const handleLoad = useCallback(async () => {
    setError('')
    if (!timestamp) return
    const data = await api(`/api/db/candle/${timestamp}?tf=${tf}`)
    if (data && !data.error) {
      setCandle(data)
    } else {
      setCandle(null)
      setError(data?.error || 'Candle not found')
    }
  }, [timestamp, tf])

  return (
    <Panel title="Feature Inspector" className="inspector-panel">
      <div className="inspector-search">
        <input
          type="text"
          placeholder="Enter candle timestamp..."
          value={timestamp}
          onChange={(e) => setTimestamp(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleLoad()}
        />
        <button onClick={handleLoad}>LOAD</button>
      </div>
      {error && <div className="inspector-error">{error}</div>}
      {candle && (
        <div className="inspector-groups">
          {GROUPS.map(g => (
            <div key={g.id} className="inspector-group">
              <div className="inspector-group-title" style={{ color: g.color }}>
                {g.name} ({g.features.length})
              </div>
              {g.features.map(f => (
                <div className="inspector-row" key={f}>
                  <span className="inspector-key">{f}</span>
                  <span className="inspector-val">{fmtFeatureValue(candle[f])}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </Panel>
  )
}
