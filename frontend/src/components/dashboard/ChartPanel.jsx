import { useEffect } from 'react'
import useCanvas from '../../hooks/useCanvas'
import { drawCandlestickChart } from '../../lib/chartRenderer'
import './ChartPanel.css'

export default function ChartPanel({ candles, structure, tf }) {
  const { canvasRef, setupCanvas } = useCanvas()

  useEffect(() => {
    if (!candles || candles.length < 2) return
    const result = setupCanvas()
    if (!result) return
    const { ctx, w, h } = result
    drawCandlestickChart(
      ctx, w, h, candles,
      structure?.swing_highs,
      structure?.swing_lows
    )
  }, [candles, structure, setupCanvas])

  return (
    <div className="chart-panel panel" style={{ gridColumn: 1, gridRow: 2 }}>
      <div className="panel-title">Candlestick Chart &mdash; BTCUSDT {tf}</div>
      <div className="panel-body">
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
