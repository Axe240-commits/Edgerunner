export default function GlowText({ children, color = 'var(--cyan)', tag = 'span', className = '' }) {
  const Tag = tag
  return (
    <Tag
      className={className}
      style={{
        color,
        textShadow: `0 0 8px ${color}`,
      }}
    >
      {children}
    </Tag>
  )
}
