import { useMemo } from 'react'
import './DataFlowBar.css'

const NODES = [
  { x: 80, label: 'BINANCE' },
  { x: 340, label: 'FEATURE ENGINE' },
  { x: 600, label: 'NEURAL NET' },
  { x: 860, label: 'STRUCTURE' },
  { x: 1100, label: 'SIGNAL' },
]

export default function DataFlowBar() {
  const svgContent = useMemo(() => {
    let html = ''
    // Lines
    for (let i = 0; i < NODES.length - 1; i++) {
      const x1 = NODES[i].x + 50
      const x2 = NODES[i + 1].x - 10
      html += `<line class="flow-line" x1="${x1}" y1="25" x2="${x2}" y2="25"/>`
    }
    // Labels
    for (const n of NODES) {
      html += `<rect x="${n.x - 45}" y="12" width="90" height="26" rx="3" fill="rgba(0,240,255,0.05)" stroke="rgba(0,240,255,0.2)" stroke-width="0.5"/>`
      html += `<text class="flow-node" x="${n.x}" y="29" text-anchor="middle">${n.label}</text>`
    }
    // Packets
    for (let i = 0; i < NODES.length - 1; i++) {
      const x1 = NODES[i].x + 50
      const x2 = NODES[i + 1].x - 10
      for (let j = 0; j < 3; j++) {
        const delay = (i * 1.5 + j * 0.6).toFixed(1)
        const dur = (1.5 + Math.random() * 0.5).toFixed(1)
        html += `<circle r="2.5" fill="#00f0ff" opacity="0">
          <animate attributeName="cx" from="${x1}" to="${x2}" dur="${dur}s" begin="${delay}s" repeatCount="indefinite"/>
          <animate attributeName="cy" values="25;23;27;25" dur="${dur}s" begin="${delay}s" repeatCount="indefinite"/>
          <animate attributeName="opacity" values="0;0.8;0.8;0" dur="${dur}s" begin="${delay}s" repeatCount="indefinite"/>
        </circle>`
      }
    }
    return html
  }, [])

  return (
    <div className="panel dataflow-panel" style={{ gridColumn: '1 / -1', gridRow: 5, minHeight: 50 }}>
      <svg
        className="dataflow-svg"
        viewBox="0 0 1200 50"
        dangerouslySetInnerHTML={{ __html: svgContent }}
      />
    </div>
  )
}
