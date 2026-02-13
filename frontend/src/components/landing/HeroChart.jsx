import { useEffect, useCallback } from 'react'
import useCanvas from '../../hooks/useCanvas'
import usePolling from '../../hooks/usePolling'
import { api } from '../../api/client'
import { drawCandlestickChart } from '../../lib/chartRenderer'
import './HeroChart.css'

export default function HeroChart() {
  const { canvasRef, setupCanvas } = useCanvas()

  const fetchCandles = useCallback(() => api('/api/candles'), [])
  const { data: candles } = usePolling(fetchCandles, 2000)

  useEffect(() => {
    if (!candles || candles.length < 2) return
    const result = setupCanvas()
    if (!result) return
    const { ctx, w, h } = result
    drawCandlestickChart(ctx, w, h, candles, null, null)
  }, [candles, setupCanvas])

  return (
    <div className="hero-chart-wrap">
      <canvas ref={canvasRef} />
    </div>
  )
}
