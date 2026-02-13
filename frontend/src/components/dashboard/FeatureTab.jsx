import { useState, useEffect, useCallback } from 'react'
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
  return new Date(ts).toISOString().replace('T', ' ').replace('.000Z', '')
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

export default function FeatureTab({ tf }) {
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(1)
  const [selectedTs, setSelectedTs] = useState(null)
  const [candle, setCandle] = useState(null)
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

  const loadCandle = useCallback(async (ts) => {
    const res = await api(`/api/db/candle/${ts}/neighbors?tf=${tf}&range=3`)
    if (res?.center) {
      setCandle(res.center)
      setSelectedTs(ts)
    }
  }, [tf])

  const handleGo = () => {
    if (tsInput) loadCandle(+tsInput)
  }

  const handleLatest = () => {
    if (data && data.length > 0) {
      loadCandle(data[0].timestamp)
    }
  }

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
      {/* Navigation Bar */}
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
        {/* Left: Candle Table */}
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

        {/* Right: Feature Groups */}
        <div className="ftab-features">
          {candle ? (
            GROUPS.map(group => (
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
            ))
          ) : (
            <div className="ftab-empty">Select a candle to view features</div>
          )}
        </div>
      </div>
    </div>
  )
}
