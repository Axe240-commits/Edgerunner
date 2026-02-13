export default function StatusDot({ color = 'var(--green)', pulse = true }) {
  return (
    <span
      style={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: color,
        animation: pulse ? 'pulse 2s ease-in-out infinite' : 'none',
        flexShrink: 0,
      }}
    />
  )
}
