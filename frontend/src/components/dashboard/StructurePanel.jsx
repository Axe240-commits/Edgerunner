import { fmt, fmtCompact } from '../../lib/formatters'
import './StructurePanel.css'

export default function StructurePanel({ structure }) {
  const cc = structure?.current_candle
  const bos = structure?.bos_events || []
  const sh = structure?.swing_highs || []
  const sl = structure?.swing_lows || []

  // Build events list
  let events = []
  for (const b of bos) {
    events.push({
      type: b.type.replace('_', ' '),
      cls: b.type.includes('BULL') ? 'bull' : 'bear',
      level: b.level,
      tag: b.body_break ? 'body break' : 'wick break',
      time: b.time || 0,
    })
  }
  for (const s of sh) {
    events.push({ type: 'SWING HIGH', cls: 'bear', level: s.price, tag: 'confirmed', time: s.time || 0 })
  }
  for (const s of sl) {
    events.push({ type: 'SWING LOW', cls: 'bull', level: s.price, tag: 'confirmed', time: s.time || 0 })
  }
  events.sort((a, b) => b.time - a.time)
  events = events.slice(0, 12)

  return (
    <div className="panel structure-panel" style={{ gridColumn: 1, gridRow: 3 }}>
      <div className="panel-title">Structure Analysis</div>
      <div className="structure-body">
        <div className="struct-col">
          {cc && (
            <>
              <div className="struct-section-title">Current Candle</div>
              <Row label="Direction" value={cc.is_bullish ? 'BULLISH' : 'BEARISH'} cls={cc.is_bullish ? 'bull' : 'bear'} />
              <Row label="Open" value={`$${fmt(cc.open, 2)}`} />
              <Row label="High" value={`$${fmt(cc.high, 2)}`} />
              <Row label="Low" value={`$${fmt(cc.low, 2)}`} />
              <Row label="Close" value={`$${fmt(cc.close, 2)}`} />
              <Row label="Volume" value={fmtCompact(cc.volume)} />
              <div className="struct-section-title">EMAs</div>
              <Row label="EMA 21" value={`$${fmt(cc.ema21, 2)}`} cls={cc.close > cc.ema21 ? 'bull' : 'bear'} />
              <Row label="EMA 50" value={`$${fmt(cc.ema50, 2)}`} cls={cc.close > cc.ema50 ? 'bull' : 'bear'} />
            </>
          )}
        </div>
        <div className="struct-col">
          <div className="struct-section-title">Events</div>
          {events.length === 0 && (
            <div style={{ color: 'var(--text-dim)', padding: '4px 0' }}>Waiting for structure breaks...</div>
          )}
          {events.map((e, i) => (
            <div className="event-line" key={i}>
              <span className={`type ${e.cls}`}>{e.type}</span>
              <span className="level">${fmt(e.level, 0)}</span>
              <span className="tag">[{e.tag}]</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function Row({ label, value, cls = '' }) {
  return (
    <div className="struct-row">
      <span className="struct-label">{label}</span>
      <span className={`struct-val ${cls}`}>{value}</span>
    </div>
  )
}
