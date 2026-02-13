import { useCallback } from 'react'
import { api } from '../../api/client'
import usePolling from '../../hooks/usePolling'
import { fmtCompact } from '../../lib/formatters'
import Panel from '../ui/Panel'
import './DbStats.css'

export default function DbStats() {
  const fetchTimeframes = useCallback(() => api('/api/timeframes'), [])
  const { data: timeframes } = usePolling(fetchTimeframes, 30000)

  return (
    <Panel title="Database Stats">
      <div className="db-stats-list">
        {timeframes && timeframes.map(tf => (
          <div className="db-stats-row" key={tf.tf}>
            <span className={`db-tf ${tf.active ? 'active' : ''}`}>{tf.tf}</span>
            <span className="db-count">{fmtCompact(tf.count)}</span>
            <span className="db-live">{tf.live_candles} live</span>
          </div>
        ))}
      </div>
    </Panel>
  )
}
