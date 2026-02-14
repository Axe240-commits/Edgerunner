import { useState, useEffect, useCallback } from 'react'
import { api, apiPost } from '../../api/client'
import './SettingsTab.css'

const TF_LIST = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1M']

const DEFAULTS = {
  macd_fast: 5,
  macd_slow: 13,
  macd_signal: 1,
  rsi_period: 14,
  atr_period: 14,
  ema_periods: [21, 50, 200],
  seeker_min_wick: 0.20,
  swing_lookback: { '1m': 2, '5m': 2, '15m': 2, '30m': 2, '1h': 2, '4h': 2, '1d': 2, '1w': 2, '1M': 2 },
}

export default function SettingsTab() {
  const [settings, setSettings] = useState(null)
  const [form, setForm] = useState(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    const data = await api('/api/settings')
    if (data) {
      setSettings(data.settings)
      setForm({ ...data.settings })
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (!form) return <div className="settings-tab panel">Loading settings...</div>

  const set = (key, val) => setForm(prev => ({ ...prev, [key]: val }))
  const setEma = (idx, val) => {
    const emas = [...(form.ema_periods || [21, 50, 200])]
    emas[idx] = val
    set('ema_periods', emas)
  }
  const setLookback = (tf, val) => {
    const lb = { ...(form.swing_lookback || DEFAULTS.swing_lookback) }
    lb[tf] = val
    set('swing_lookback', lb)
  }

  const handleSave = async () => {
    setSaving(true)
    setMessage('')
    const result = await apiPost('/api/settings', form)
    setSaving(false)
    if (result?.ok) {
      setMessage('Settings saved — Analyzers rebuilt')
      setSettings({ ...form })
    } else {
      setMessage('Error: ' + (result?.error || 'Save failed'))
    }
  }

  const handleReset = () => {
    setForm({ ...DEFAULTS })
    setMessage('Reset to defaults (unsaved)')
  }

  const emas = form.ema_periods || [21, 50, 200]
  const lookbacks = form.swing_lookback || DEFAULTS.swing_lookback

  return (
    <div className="settings-tab panel">
      <div className="settings-header">
        <h2>ANALYZER SETTINGS</h2>
        <div className="settings-actions">
          <button className="stab-btn stab-btn--ghost" onClick={handleReset}>Reset to Defaults</button>
          <button className="stab-btn stab-btn--primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save & Rebuild'}
          </button>
        </div>
      </div>
      {message && <div className={`settings-msg ${message.startsWith('Error') ? 'err' : 'ok'}`}>{message}</div>}

      <div className="settings-grid">
        <div className="settings-group">
          <h3>MACD</h3>
          <label>
            <span>Fast Period</span>
            <input type="number" min={1} max={50} value={form.macd_fast || 5}
              onChange={e => set('macd_fast', +e.target.value)} />
          </label>
          <label>
            <span>Slow Period</span>
            <input type="number" min={1} max={100} value={form.macd_slow || 13}
              onChange={e => set('macd_slow', +e.target.value)} />
          </label>
          <label>
            <span>Signal Period</span>
            <input type="number" min={1} max={50} value={form.macd_signal || 1}
              onChange={e => set('macd_signal', +e.target.value)} />
          </label>
        </div>

        <div className="settings-group">
          <h3>INDICATORS</h3>
          <label>
            <span>RSI Period</span>
            <input type="number" min={2} max={100} value={form.rsi_period || 14}
              onChange={e => set('rsi_period', +e.target.value)} />
          </label>
          <label>
            <span>ATR Period</span>
            <input type="number" min={2} max={100} value={form.atr_period || 14}
              onChange={e => set('atr_period', +e.target.value)} />
          </label>
        </div>

        <div className="settings-group">
          <h3>EMAs</h3>
          <label>
            <span>EMA Fast</span>
            <input type="number" min={1} max={500} value={emas[0]}
              onChange={e => setEma(0, +e.target.value)} />
          </label>
          <label>
            <span>EMA Mid</span>
            <input type="number" min={1} max={500} value={emas[1]}
              onChange={e => setEma(1, +e.target.value)} />
          </label>
          <label>
            <span>EMA Slow</span>
            <input type="number" min={1} max={500} value={emas[2]}
              onChange={e => setEma(2, +e.target.value)} />
          </label>
        </div>

        <div className="settings-group">
          <h3>SEEKER</h3>
          <label>
            <span>Min Wick %</span>
            <input type="number" min={0.01} max={1.0} step={0.01}
              value={form.seeker_min_wick ?? 0.20}
              onChange={e => set('seeker_min_wick', +e.target.value)} />
          </label>
        </div>

        <div className="settings-group settings-group--wide">
          <h3>SWING LOOKBACK (per TF)</h3>
          <div className="lookback-grid">
            {TF_LIST.map(tf => (
              <label key={tf}>
                <span>{tf}</span>
                <input type="number" min={1} max={20}
                  value={lookbacks[tf] ?? 5}
                  onChange={e => setLookback(tf, +e.target.value)} />
              </label>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
