import { fmt, fmtCompact } from '../../lib/formatters'
import './CandleStream.css'

export default function CandleStream({ candles }) {
  if (!candles || candles.length === 0) return null
  const latest = candles.slice(-12).reverse()

  return (
    <div className="panel" style={{ gridColumn: '1 / 3', gridRow: 4, minHeight: 64 }}>
      <div className="panel-title">Candle Stream</div>
      <div className="candle-stream">
        {latest.map((c, i) => {
          const bull = c.close >= c.open
          return (
            <div className="candle-card" key={i}>
              <div className={`dir ${bull ? 'bull' : 'bear'}`}>{bull ? 'BULL' : 'BEAR'}</div>
              <div className="ohlc">
                O {fmt(c.open, 0)} H {fmt(c.high, 0)}<br />
                L {fmt(c.low, 0)} C {fmt(c.close, 0)}
              </div>
              <div className="vol">Vol {fmtCompact(c.volume)}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
