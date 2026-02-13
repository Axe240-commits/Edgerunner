import { useState, useCallback } from 'react'
import { api, apiPost } from '../../api/client'
import usePolling from '../../hooks/usePolling'
import DashboardHeader from './DashboardHeader'
import ChartPanel from './ChartPanel'
import NeuralPanel from './NeuralPanel'
import FeaturePanel from './FeaturePanel'
import StructurePanel from './StructurePanel'
import SignalPanel from './SignalPanel'
import WhalePanel from './WhalePanel'
import CandleStream from './CandleStream'
import SystemPanel from './SystemPanel'
import DataFlowBar from './DataFlowBar'
import SettingsTab from './SettingsTab'
import ScenarioTab from './ScenarioTab'
import FeatureTab from './FeatureTab'
import ThreadTab from './ThreadTab'
import './DashboardLayout.css'

export default function DashboardLayout() {
  const [tf, setTf] = useState('1m')
  const [activeTab, setActiveTab] = useState('overview')

  const fetchTicker = useCallback(() => api('/api/ticker'), [])
  const fetchSparkline = useCallback(() => api('/api/sparkline'), [])
  const fetchCandles = useCallback(() => api(`/api/candles?tf=${tf}`), [tf])
  const fetchFeatures = useCallback(() => api(`/api/features/stream?tf=${tf}`), [tf])
  const fetchFeatStatus = useCallback(() => api(`/api/features/status?tf=${tf}`), [tf])
  const fetchStructure = useCallback(() => api(`/api/structure?tf=${tf}`), [tf])
  const fetchSignal = useCallback(() => api('/api/signal'), [])
  const fetchNeural = useCallback(() => api('/api/neural/status'), [])
  const fetchWhales = useCallback(() => api('/api/whales'), [])
  const fetchSystem = useCallback(() => api('/api/system'), [])
  const fetchStats = useCallback(() => api('/api/stats'), [])
  const fetchTimeframes = useCallback(() => api('/api/timeframes'), [])

  const { data: ticker } = usePolling(fetchTicker, 1000)
  const { data: sparkline } = usePolling(fetchSparkline, 2000)
  const { data: candles } = usePolling(fetchCandles, 2000)
  const { data: features } = usePolling(fetchFeatures, 2000)
  const { data: featStatus } = usePolling(fetchFeatStatus, 2000)
  const { data: structure } = usePolling(fetchStructure, 3000)
  const { data: signal } = usePolling(fetchSignal, 1000)
  const { data: neural } = usePolling(fetchNeural, 2000)
  const { data: whales } = usePolling(fetchWhales, 5000)
  const { data: system } = usePolling(fetchSystem, 5000)
  const { data: stats } = usePolling(fetchStats, 3000)
  const { data: timeframes } = usePolling(fetchTimeframes, 10000)

  const handleTfChange = async (newTf) => {
    setTf(newTf)
    await apiPost('/api/set_tf', { tf: newTf })
  }

  return (
    <div className={`dashboard ${activeTab !== 'overview' ? 'dashboard--single' : ''}`}>
      <DashboardHeader
        ticker={ticker}
        sparkline={sparkline}
        stats={stats}
        tf={tf}
        timeframes={timeframes}
        onTfChange={handleTfChange}
        activeTab={activeTab}
        onTabChange={setActiveTab}
      />
      {activeTab === 'overview' && (
        <>
          <ChartPanel candles={candles} structure={structure} tf={tf} />
          <NeuralPanel neural={neural} />
          <FeaturePanel features={features} status={featStatus} />
          <StructurePanel structure={structure} />
          <SignalPanel signal={signal} />
          <WhalePanel whales={whales} />
          <CandleStream candles={candles} />
          <SystemPanel system={system} />
          <DataFlowBar />
        </>
      )}
      {activeTab === 'settings' && <SettingsTab tf={tf} />}
      {activeTab === 'scenarios' && <ScenarioTab tf={tf} />}
      {activeTab === 'features' && <FeatureTab tf={tf} />}
      {activeTab === 'threads' && <ThreadTab />}
    </div>
  )
}
