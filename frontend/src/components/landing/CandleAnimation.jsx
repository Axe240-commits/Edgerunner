import { useEffect, useRef } from 'react'
import useCanvas from '../../hooks/useCanvas'
import { drawLiveChart } from '../../lib/candleAnimation'
import './CandleAnimation.css'

export default function CandleAnimation({ candles, candles15m, featureValues, signal, tickerPrice, liquidations }) {
  const { canvasRef, setupCanvas } = useCanvas()
  const rafRef = useRef(null)
  const dataRef = useRef({ candles, candles15m, featureValues, signal, tickerPrice, liquidations })
  dataRef.current = { candles, candles15m, featureValues, signal, tickerPrice, liquidations }

  useEffect(() => {
    let running = true

    function loop(timestamp) {
      if (!running) return
      const result = setupCanvas()
      if (result) {
        const { candles: c, candles15m: c15, featureValues: fv, signal: s, tickerPrice: tp, liquidations: liq } = dataRef.current
        drawLiveChart(result.ctx, result.w, result.h, timestamp, c, fv, s, tp, c15, liq)
      }
      rafRef.current = requestAnimationFrame(loop)
    }

    rafRef.current = requestAnimationFrame(loop)
    return () => {
      running = false
      cancelAnimationFrame(rafRef.current)
    }
  }, [setupCanvas])

  return (
    <div className="candle-animation-wrap">
      <canvas ref={canvasRef} />
    </div>
  )
}
