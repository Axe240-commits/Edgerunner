import { useCallback } from 'react'
import { api } from '../../api/client'
import usePolling from '../../hooks/usePolling'
import './SkullWatcherTab.css'

function fmtUsd(value) {
  const n = Number(value || 0)
  if (!Number.isFinite(n)) return '$0'
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`
  if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(1)}k`
  return `$${n.toFixed(0)}`
}

function fmtNum(value) {
  const n = Number(value || 0)
  if (!Number.isFinite(n)) return '0'
  return n.toLocaleString('en-US')
}

function fmtTs(value) {
  if (!value) return '--'
  if (typeof value === 'string') {
    const d = new Date(value)
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleString('de-DE', { timeZone: 'Europe/Berlin' })
    }
    return value
  }
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '--'
  const ms = n > 1e12 ? n : n * 1000
  return new Date(ms).toLocaleString('de-DE', { timeZone: 'Europe/Berlin' })
}

function distClass(distancePct) {
  if (distancePct <= 2) return 'crit'
  if (distancePct <= 5) return 'warn'
  return 'ok'
}

function riskClass(riskScore, threshold) {
  if (riskScore >= threshold * 2) return 'crit'
  if (riskScore >= threshold) return 'warn'
  return 'ok'
}

export default function SkullWatcherTab() {
  const fetchSnapshot = useCallback(
    () => api('/api/skullwatcher?positions=40&alerts=50&signals=40'),
    [],
  )
  const { data, loading, error } = usePolling(fetchSnapshot, 5000)

  if (loading && !data) {
    return (
      <div className="skull-tab panel">
        <h2>SKULLWATCHER</h2>
        <div className="skull-empty">Loading SkullWatcher snapshot...</div>
      </div>
    )
  }

  if (!data?.available) {
    return (
      <div className="skull-tab panel">
        <h2>SKULLWATCHER</h2>
        <div className="skull-empty">
          <div>Shadow Tracker DB not available.</div>
          <div className="skull-dim">{data?.error || 'No database found'}</div>
          {Array.isArray(data?.db_candidates) && data.db_candidates.length > 0 && (
            <div className="skull-dim">
              Candidates:
              <br />
              {data.db_candidates.join('\n')}
            </div>
          )}
        </div>
      </div>
    )
  }

  const summary = data.summary || {}
  const counts = summary.alert_counts || {}
  const positions = data.positions || []
  const alerts = data.alerts || []
  const whaleSignals = data.whale_signals || []
  const threshold = Number(data.risk_threshold || 100000)

  return (
    <div className="skull-tab panel">
      <div className="skull-head">
        <h2>SKULLWATCHER</h2>
        <div className="skull-meta">
          <span>DB: {data.db_path}</span>
          <span>Updated: {fmtTs(data.db_updated_at)}</span>
          <span>{fmtNum(data.db_size_mb)} MB</span>
        </div>
      </div>

      <div className="skull-cards">
        <div className="skull-card">
          <div className="label">Tracked Positions</div>
          <div className="value">{fmtNum(summary.tracked_positions)}</div>
        </div>
        <div className="skull-card">
          <div className="label">High Risk (&gt;= {fmtNum(threshold)})</div>
          <div className="value warn">{fmtNum(summary.high_risk_positions)}</div>
        </div>
        <div className="skull-card">
          <div className="label">Skull Alerts (24h)</div>
          <div className="value">{fmtNum(summary.recent_alerts_24h)}</div>
        </div>
        <div className="skull-card">
          <div className="label">LIQ_RISK Total</div>
          <div className="value">{fmtNum(counts.LIQUIDATION_RISK)}</div>
        </div>
        <div className="skull-card">
          <div className="label">FULL_CLOSE Total</div>
          <div className="value">{fmtNum(counts.FULL_CLOSE)}</div>
        </div>
        <div className="skull-card">
          <div className="label">CAPITULATION Total</div>
          <div className="value">{fmtNum(counts.CAPITULATION)}</div>
        </div>
      </div>

      <div className="skull-body">
        <section className="skull-section">
          <div className="section-title">High-Risk Positions</div>
          <div className="skull-table-wrap">
            <table className="skull-table">
              <thead>
                <tr>
                  <th>Wallet</th>
                  <th>Coin</th>
                  <th>Dir</th>
                  <th>USD</th>
                  <th>Dist</th>
                  <th>Risk</th>
                  <th>Lev</th>
                  <th>Ts</th>
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 && (
                  <tr><td colSpan={8} className="empty">No tracked positions</td></tr>
                )}
                {positions.map((p) => (
                  <tr key={`${p.wallet_full}:${p.coin}`}>
                    <td>{p.wallet}</td>
                    <td>{p.coin}</td>
                    <td className={p.direction === 'LONG' ? 'up' : 'down'}>{p.direction}</td>
                    <td>{fmtUsd(p.size_usd)}</td>
                    <td className={distClass(p.distance_pct)}>{Number(p.distance_pct).toFixed(2)}%</td>
                    <td className={riskClass(Number(p.risk_score), threshold)}>{fmtNum(p.risk_score)}</td>
                    <td>{p.leverage}x</td>
                    <td>{fmtTs(p.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="skull-section">
          <div className="section-title">Skull Alerts</div>
          <div className="skull-feed">
            {alerts.length === 0 && <div className="feed-empty">No Skull alerts</div>}
            {alerts.map((a, i) => (
              <div className="feed-item" key={`${a.type}:${a.triggered_at}:${i}`}>
                <div className="row-a">
                  <span className={`badge type-${String(a.type || '').toLowerCase()}`}>{a.type}</span>
                  <span className="coin">{a.coin}</span>
                  <span className="time">{fmtTs(a.triggered_at)}</span>
                </div>
                <div className="row-b">
                  <span className="wallet">{a.wallet || '-'}</span>
                  <span className="msg">{a.message}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="skull-section">
          <div className="section-title">Recent Whale Signals</div>
          <div className="skull-table-wrap">
            <table className="skull-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Signal</th>
                  <th>Side</th>
                  <th>USD</th>
                  <th>Wallet</th>
                  <th>Tier</th>
                </tr>
              </thead>
              <tbody>
                {whaleSignals.length === 0 && (
                  <tr><td colSpan={6} className="empty">No whale signals</td></tr>
                )}
                {whaleSignals.map((s, i) => (
                  <tr key={`${s.timestamp}:${s.wallet_full}:${i}`}>
                    <td>{fmtTs(s.timestamp_iso || s.timestamp)}</td>
                    <td>{s.signal_type}</td>
                    <td className={s.side === 'BUY' ? 'up' : 'down'}>{s.side}</td>
                    <td>{fmtUsd(s.notional_usd)}</td>
                    <td>{s.wallet}</td>
                    <td>{s.tier}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {error && <div className="skull-error">Polling error: {String(error)}</div>}
    </div>
  )
}
