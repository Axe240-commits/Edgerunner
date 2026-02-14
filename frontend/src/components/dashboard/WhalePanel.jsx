import { useState, useCallback } from 'react'
import { api } from '../../api/client'
import usePolling from '../../hooks/usePolling'
import { fmtCompact } from '../../lib/formatters'
import './WhalePanel.css'

const fmtWTs = (ts) => {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return d.toLocaleString('sv-SE', { timeZone: 'Europe/Berlin' }).slice(5)
}

const fmtLiqTs = (ts) => {
  if (!ts) return '—'
  // event_time is 'YYYY-MM-DD HH:MM:SS' string (UTC)
  const d = new Date(ts + 'Z')
  return d.toLocaleString('sv-SE', { timeZone: 'Europe/Berlin' }).slice(5)
}

export default function WhalePanel({ whales }) {
  const [tab, setTab] = useState('activity')
  const fetchLive = useCallback(() => api('/api/whales/live?limit=30'), [])
  const { data } = usePolling(fetchLive, 10000)

  const activity = data?.activity || []
  const pressure = data?.pressure
  const liquidations = data?.liquidations || []

  return (
    <div className="panel whale-panel" style={{ gridColumn: 3, gridRow: '3 / 5' }}>
      <div className="panel-title">Whale Tracker</div>

      {/* Pressure Bar */}
      {pressure && (
        <div className="whale-pressure">
          <div className="wp-bar">
            <div className="wp-buy" style={{ width: `${pressure.buy_pct}%` }}>
              <span>{pressure.buy_pct}%</span>
            </div>
            <div className="wp-sell" style={{ width: `${pressure.sell_pct}%` }}>
              <span>{pressure.sell_pct}%</span>
            </div>
          </div>
          <div className="wp-stats">
            <span className="wp-buy-stat">
              BUY {pressure.buy_count}x · ${fmtCompact(pressure.buy_volume)}
              <span className={pressure.buy_avg_pnl_1h >= 0 ? 'up' : 'down'}>
                {' '}{pressure.buy_avg_pnl_1h >= 0 ? '+' : ''}{pressure.buy_avg_pnl_1h}% 1h
              </span>
            </span>
            <span className={`wp-net ${pressure.net.toLowerCase()}`}>{pressure.net}</span>
            <span className="wp-sell-stat">
              SELL {pressure.sell_count}x · ${fmtCompact(pressure.sell_volume)}
              <span className={pressure.sell_avg_pnl_1h >= 0 ? 'up' : 'down'}>
                {' '}{pressure.sell_avg_pnl_1h >= 0 ? '+' : ''}{pressure.sell_avg_pnl_1h}% 1h
              </span>
            </span>
          </div>
        </div>
      )}

      {/* Tab switch */}
      <div className="whale-tabs">
        <button className={`wt-btn ${tab === 'activity' ? 'active' : ''}`}
          onClick={() => setTab('activity')}>Activity ({activity.length})</button>
        <button className={`wt-btn ${tab === 'liqs' ? 'active' : ''}`}
          onClick={() => setTab('liqs')}>Liquidations ({liquidations.length})</button>
      </div>

      {/* Activity Feed */}
      {tab === 'activity' && (
        <div className="whale-feed">
          <div className="whale-header">
            <span className="we-time">ZEIT</span>
            <span className="we-side">SIDE</span>
            <span className="we-signal">SIGNAL</span>
            <span className="we-size">VOLUMEN</span>
            <span className="we-price">PREIS</span>
            <span className="we-pnl">PNL 5M</span>
            <span className="we-pnl">PNL 1H</span>
            <span className="we-lev">LEV</span>
          </div>
          {activity.length === 0 && <div className="whale-empty">No whale data</div>}
          {activity.map((w, i) => {
            const isBuy = w.side === 'BUY'
            const pnl5m = w.pnl_5m
            const pnl1h = w.pnl_1h
            const signal = w.signal_type !== 'UNKNOWN' ? w.signal_type : null
            return (
              <div className="whale-entry" key={i}>
                <span className="we-time">{fmtWTs(w.timestamp)}</span>
                <span className={`we-side ${isBuy ? 'buy' : 'sell'}`}>
                  {w.side}
                </span>
                <span className={`we-signal ${signal ? (signal.includes('LONG') || signal === 'SHORT_COVER' ? 'bull' : 'bear') : ''}`}>
                  {signal || '—'}
                </span>
                <span className="we-size">${fmtCompact(w.notional_usd)}</span>
                <span className="we-price">{w.price_at_event?.toFixed(0)}</span>
                <span className={`we-pnl ${(pnl5m ?? 0) >= 0 ? 'up' : 'down'}`}>
                  {pnl5m != null ? `${pnl5m >= 0 ? '+' : ''}${pnl5m.toFixed(2)}%` : '—'}
                </span>
                <span className={`we-pnl ${(pnl1h ?? 0) >= 0 ? 'up' : 'down'}`}>
                  {pnl1h != null ? `${pnl1h >= 0 ? '+' : ''}${pnl1h.toFixed(2)}%` : '—'}
                </span>
                <span className="we-lev">
                  {w.leverage ? `${w.leverage}x` : '—'}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* Liquidations */}
      {tab === 'liqs' && (
        <div className="whale-feed">
          <div className="whale-header">
            <span className="we-time">ZEIT</span>
            <span className="we-side">TYP</span>
            <span className="we-size">GRÖSSE</span>
            <span className="we-price">LIQ PREIS</span>
            <span className="we-price-dim">MARKT</span>
          </div>
          {liquidations.length === 0 && <div className="whale-empty">No liquidation data</div>}
          {liquidations.map((l, i) => {
            const isLong = l.direction === 'LONG'
            return (
              <div className="whale-entry" key={i}>
                <span className="we-time">{fmtLiqTs(l.event_time)}</span>
                <span className={`we-side ${isLong ? 'sell' : 'buy'}`}>
                  LIQ {l.direction}
                </span>
                <span className="we-size">${fmtCompact(l.position_size_usd)}</span>
                <span className="we-price">@{l.liq_price?.toFixed(0)}</span>
                <span className="we-price-dim">{l.price_at_event?.toFixed(0)}</span>
                {l.is_cascade_part === 1 && (
                  <span className="we-cascade">CASCADE</span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
