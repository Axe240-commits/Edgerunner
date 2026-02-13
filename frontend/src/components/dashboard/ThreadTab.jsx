import { useState, useCallback } from 'react'
import { api, apiPost } from '../../api/client'
import usePolling from '../../hooks/usePolling'
import './ThreadTab.css'

export default function ThreadTab() {
  const [message, setMessage] = useState('')
  const fetchThreads = useCallback(() => api('/api/threads'), [])
  const { data } = usePolling(fetchThreads, 2000)

  const threads = data?.threads || []
  const enabledTfs = new Set(data?.enabled_tfs || [])
  const allTfs = data?.all_tfs || []

  const control = async (action, thread) => {
    setMessage('')
    const res = await apiPost('/api/threads/control', { action, thread })
    if (!res?.ok) setMessage('Error: ' + (res?.error || 'Failed'))
  }

  const setInterval_ = async (thread, interval) => {
    const res = await apiPost('/api/threads/control', { action: 'set_interval', thread, interval })
    if (!res?.ok) setMessage('Error: ' + (res?.error || 'Failed'))
  }

  const toggleTf = async (tf, enable) => {
    const action = enable ? 'enable_tf' : 'disable_tf'
    const res = await apiPost('/api/threads/control', { action, tf })
    if (!res?.ok) setMessage('Error: ' + (res?.error || 'Failed'))
  }

  return (
    <div className="thread-tab panel">
      <h2>THREAD CONTROL</h2>
      {message && <div className="thread-msg">{message}</div>}

      <table className="thread-table">
        <thead>
          <tr>
            <th>Thread</th>
            <th>Status</th>
            <th>Interval (s)</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {threads.map(t => (
            <tr key={t.name}>
              <td className="thread-name">{t.name}</td>
              <td>
                <span className={`thread-status ${t.running ? 'live' : 'paused'}`}>
                  {t.running ? 'LIVE' : 'PAUSED'}
                </span>
              </td>
              <td>
                <input type="number" className="thread-interval" min={0.5} step={0.5}
                  defaultValue={t.interval}
                  onBlur={e => setInterval_(t.name, +e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && setInterval_(t.name, +e.target.value)} />
              </td>
              <td className="thread-actions">
                {t.running ? (
                  <button className="stab-btn stab-btn--ghost" onClick={() => control('pause', t.name)}>Pause</button>
                ) : (
                  <button className="stab-btn stab-btn--primary" onClick={() => control('resume', t.name)}>Resume</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="tf-toggles">
        <h3>TIMEFRAME TOGGLES (HL Poller)</h3>
        <div className="tf-toggle-grid">
          {allTfs.map(tf => {
            const enabled = enabledTfs.has(tf)
            return (
              <label key={tf} className="tf-toggle">
                <input type="checkbox" checked={enabled}
                  onChange={e => toggleTf(tf, e.target.checked)} />
                <span className={enabled ? 'tf-on' : 'tf-off'}>{tf}</span>
              </label>
            )
          })}
        </div>
      </div>
    </div>
  )
}
