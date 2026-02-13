import { useEffect, useRef, useState } from 'react'
import useAuth from '../../hooks/useAuth'
import { fmt, fmtCompact, fmtUptime } from '../../lib/formatters'
import { drawSparkline } from '../../lib/chartRenderer'
import StatusDot from '../ui/StatusDot'
import CyberButton from '../ui/CyberButton'
import TabBar from './TabBar'
import './DashboardHeader.css'

const TF_LIST = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1M']

export default function DashboardHeader({ ticker, sparkline, stats, tf, timeframes, onTfChange, activeTab, onTabChange }) {
  const { logout } = useAuth()
  const sparkRef = useRef(null)
  const [clock, setClock] = useState('')

  useEffect(() => {
    const update = () => setClock(new Date().toLocaleTimeString('en-US', { hour12: false }))
    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (!sparkRef.current || !sparkline) return
    const canvas = sparkRef.current
    const ctx = canvas.getContext('2d')
    drawSparkline(ctx, canvas.width / 2, canvas.height / 2, sparkline)
  }, [sparkline])

  const price = ticker?.price || 0
  const change = ticker?.change_24h || 0

  return (
    <div className="dash-header panel" style={{ gridColumn: '1 / -1' }}>
      <div className="header-top">
      <div className="header-left">
        <div className="logo">
          <span className="logo-edge">EDGE</span>
          <span className="logo-runner">RUNNER</span>
        </div>
        <StatusDot />
        <div className="tf-selector">
          {TF_LIST.map(t => (
            <button
              key={t}
              className={`tf-btn ${t === tf ? 'active' : ''}`}
              onClick={() => onTfChange(t)}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div className="header-center">
        <div className="price-display">
          <span className="price-value">${fmt(price, 2)}</span>
          <span className={`price-change ${change >= 0 ? 'up' : 'down'}`}>
            {change >= 0 ? '+' : ''}{change.toFixed(2)}%
          </span>
        </div>
        <canvas
          ref={sparkRef}
          className="sparkline-canvas"
          width={240}
          height={64}
        />
      </div>

      <div className="header-right">
        <div className="stats-row">
          <span>CANDLES <span className="stat-val">{fmtCompact(stats?.candles_processed || 0)}</span></span>
          <span>PATTERNS <span className="stat-val">{fmtCompact(stats?.patterns_detected || 0)}</span></span>
          <span>UPTIME <span className="stat-val">{fmtUptime(stats?.uptime_seconds || 0)}</span></span>
        </div>
        <span className="clock">{clock}</span>
        <CyberButton variant="ghost" onClick={logout}>Logout</CyberButton>
      </div>
      </div>
      <TabBar activeTab={activeTab} onTabChange={onTabChange} />
    </div>
  )
}
