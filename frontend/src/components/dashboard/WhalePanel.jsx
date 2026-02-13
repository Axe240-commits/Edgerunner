import { fmtCompact, fmtTime } from '../../lib/formatters'
import './WhalePanel.css'

export default function WhalePanel({ whales }) {
  const feed = whales?.feed?.slice(0, 20) || []

  return (
    <div className="panel" style={{ gridColumn: 3, gridRow: 3 }}>
      <div className="panel-title">Whale Activity Feed</div>
      <div className="whale-feed">
        {feed.map((w, i) => (
          <div className="whale-entry" key={i} style={{ animationDelay: `${i * 0.05}s` }}>
            <span className={`tier-dot ${w.tier}`} />
            <span className="whale-addr">{w.address}</span>
            <span className="whale-action">{w.action}</span>
            <span className="whale-size">${fmtCompact(w.size_usd)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
