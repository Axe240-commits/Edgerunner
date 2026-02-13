export function fmt(n, d = 2) {
  if (n === null || n === undefined) return '\u2014'
  return Number(n).toLocaleString('en-US', {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  })
}

export function fmtCompact(n) {
  if (n === null || n === undefined) return '\u2014'
  const abs = Math.abs(n)
  if (abs >= 1e9) return (n / 1e9).toFixed(1) + 'B'
  if (abs >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'K'
  return n.toFixed(0)
}

export function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString('en-US', { hour12: false })
}

export function fmtUptime(s) {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

export function fmtFeatureValue(v) {
  if (v === null || v === undefined) return '\u2014'
  if (typeof v === 'boolean') return v ? '1' : '0'
  if (Number.isInteger(v)) return String(v)
  if (typeof v === 'number') {
    if (Math.abs(v) > 1000) return fmt(v, 2)
    if (Math.abs(v) > 1) return v.toFixed(4)
    return v.toFixed(6)
  }
  return String(v)
}
