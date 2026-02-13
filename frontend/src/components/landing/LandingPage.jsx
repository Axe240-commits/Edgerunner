import { useCallback } from 'react'
import { api } from '../../api/client'
import usePolling from '../../hooks/usePolling'
import Header from './Header'
import Footer from './Footer'
import CandleAnimation from './CandleAnimation'
import './LandingPage.css'

export default function LandingPage() {
  const fetchTicker = useCallback(() => api('/api/ticker'), [])
  const fetchCandles = useCallback(() => api('/api/candles'), [])
  const fetchCandles15m = useCallback(() => api('/api/candles?tf=15m'), [])
  const fetchFeatures = useCallback(() => api('/api/features/status'), [])
  const fetchSignal = useCallback(() => api('/api/signal'), [])
  const fetchLiqs = useCallback(() => api('/api/liquidations'), [])

  const { data: ticker } = usePolling(fetchTicker, 2000)
  const { data: candles } = usePolling(fetchCandles, 2000)
  const { data: candles15m } = usePolling(fetchCandles15m, 5000)
  const { data: features } = usePolling(fetchFeatures, 3000)
  const { data: signal } = usePolling(fetchSignal, 3000)
  const { data: liquidations } = usePolling(fetchLiqs, 30000)

  return (
    <div className="landing">
      <Header ticker={ticker} />
      <main className="landing-hero">
        <div className="hero-content">
          <CandleAnimation
            candles={candles}
            candles15m={candles15m}
            featureValues={features?.values}
            signal={signal}
            tickerPrice={ticker?.price}
            liquidations={liquidations?.stop_predictions}
          />
        </div>
      </main>
      <Footer />
    </div>
  )
}
