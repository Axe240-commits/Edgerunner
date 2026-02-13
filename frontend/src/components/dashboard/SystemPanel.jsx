import ProgressBar from '../ui/ProgressBar'
import './SystemPanel.css'

export default function SystemPanel({ system }) {
  if (!system) return null

  const gpuPct = system.gpu_usage || 0
  const ramPct = system.ram_total > 0 ? (system.ram_used / system.ram_total * 100) : 0
  const cpuPct = system.cpu_usage || 0
  const diskPct = system.disk_total > 0 ? (system.disk_used / system.disk_total * 100) : 0

  return (
    <div className="panel" style={{ gridColumn: 3, gridRow: 4, minHeight: 64 }}>
      <div className="panel-title">System Health</div>
      <div className="sys-rows">
        <SysRow
          label={`GPU ${system.gpu_name || ''}${system.gpu_temp ? ' ' + system.gpu_temp + '\u00B0C' : ''}`}
          pct={gpuPct}
        />
        <SysRow
          label={`RAM ${system.ram_used?.toFixed(1) || 0}/${system.ram_total?.toFixed(0) || 0}GB`}
          pct={ramPct}
        />
        <SysRow label="CPU" pct={cpuPct} />
        <SysRow label="DISK" pct={diskPct} />
      </div>
    </div>
  )
}

function SysRow({ label, pct }) {
  return (
    <div className="sys-row">
      <span className="sys-label">{label}</span>
      <ProgressBar value={pct} />
      <span className="sys-pct">{pct.toFixed(0)}%</span>
    </div>
  )
}
