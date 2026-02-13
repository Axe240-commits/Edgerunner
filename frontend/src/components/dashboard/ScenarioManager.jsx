import { useState, useCallback } from 'react'
import { api, apiPost } from '../../api/client'
import usePolling from '../../hooks/usePolling'
import Panel from '../ui/Panel'
import CyberButton from '../ui/CyberButton'
import './ScenarioManager.css'

export default function ScenarioManager() {
  const fetchScenarios = useCallback(() => api('/api/scenarios'), [])
  const { data: scenarios } = usePolling(fetchScenarios, 30000)

  const [filterResult, setFilterResult] = useState(null)
  const [filterParams, setFilterParams] = useState({
    bos_bull: '',
    bos_bear: '',
    is_seeker_kill: '',
  })

  const handleFilter = async () => {
    const params = {}
    for (const [k, v] of Object.entries(filterParams)) {
      if (v !== '') params[k] = Number(v)
    }
    const result = await apiPost('/api/scenarios/filter', { ...params, timeframe: '1m' })
    setFilterResult(result)
  }

  return (
    <Panel title="Scenario Manager" className="scenario-panel">
      <div className="scenario-filter">
        <div className="scenario-filter-title">Filter Candles</div>
        <div className="scenario-fields">
          {Object.entries(filterParams).map(([k, v]) => (
            <div className="scenario-field" key={k}>
              <label>{k}</label>
              <select value={v} onChange={(e) => setFilterParams(p => ({ ...p, [k]: e.target.value }))}>
                <option value="">any</option>
                <option value="1">1 (true)</option>
                <option value="0">0 (false)</option>
              </select>
            </div>
          ))}
        </div>
        <CyberButton variant="ghost" onClick={handleFilter}>Filter</CyberButton>
        {filterResult && (
          <div className="scenario-result">
            Found {filterResult.count} candles
          </div>
        )}
      </div>

      <div className="scenario-list">
        <div className="scenario-list-title">Saved Scenarios</div>
        {(!scenarios || scenarios.length === 0) && (
          <div className="scenario-empty">No scenarios saved yet</div>
        )}
        {scenarios && scenarios.map((s, i) => (
          <div className="scenario-item" key={i}>
            <span className="scenario-name">{s.name}</span>
            <span className="scenario-desc">{s.description}</span>
          </div>
        ))}
      </div>
    </Panel>
  )
}
