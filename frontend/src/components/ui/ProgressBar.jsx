export default function ProgressBar({ value = 0, color = 'var(--cyan)', height = 8 }) {
  const pct = Math.max(0, Math.min(100, value))
  const cls = pct > 85 ? 'crit' : pct > 60 ? 'warn' : ''
  return (
    <div style={{
      flex: 1,
      height,
      background: 'rgba(255,255,255,0.05)',
      borderRadius: 2,
      overflow: 'hidden',
    }}>
      <div
        className={cls}
        style={{
          height: '100%',
          width: `${pct}%`,
          borderRadius: 2,
          background: cls === 'crit' ? 'var(--red)' : cls === 'warn' ? 'var(--amber)' : color,
          transition: 'width 0.5s ease',
        }}
      />
    </div>
  )
}
