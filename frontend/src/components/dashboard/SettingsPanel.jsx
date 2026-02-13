import Panel from '../ui/Panel'
import './SettingsPanel.css'

const ANALYZER_PARAMS = [
  { label: 'Swing Lookback', value: '5 (1m/5m), 3 (15m-4h), 2 (1d-1M)' },
  { label: 'MACD Fast', value: '5' },
  { label: 'MACD Slow', value: '13' },
  { label: 'MACD Signal', value: '1' },
  { label: 'RSI Period', value: '14' },
  { label: 'ATR Period', value: '14' },
  { label: 'EMA Periods', value: '21, 50, 200' },
  { label: 'Seeker Min Wick', value: '20%' },
  { label: 'Feature Count', value: '89' },
]

export default function SettingsPanel() {
  return (
    <Panel title="Analyzer Settings">
      <div className="settings-list">
        {ANALYZER_PARAMS.map(p => (
          <div className="settings-row" key={p.label}>
            <span className="settings-label">{p.label}</span>
            <span className="settings-value">{p.value}</span>
          </div>
        ))}
      </div>
    </Panel>
  )
}
