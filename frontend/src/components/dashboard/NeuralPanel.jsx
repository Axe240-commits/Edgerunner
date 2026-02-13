import { useEffect, useRef } from 'react'
import useCanvas from '../../hooks/useCanvas'
import './NeuralPanel.css'

const NN_LAYERS = [89, 32, 16, 8, 1]
const LABELS = ['INPUT', 'HIDDEN-1', 'HIDDEN-2', 'HIDDEN-3', 'OUTPUT']

export default function NeuralPanel({ neural }) {
  const { canvasRef, setupCanvas } = useCanvas()
  const nodesRef = useRef(null)
  const connsRef = useRef(null)
  const rafRef = useRef(null)

  useEffect(() => {
    let running = true

    function initNet(w, h) {
      const padding = 30
      const nodes = []
      const conns = []

      for (let l = 0; l < NN_LAYERS.length; l++) {
        const count = NN_LAYERS[l]
        const maxShow = Math.min(count, l === 0 ? 20 : count)
        const x = padding + (l / (NN_LAYERS.length - 1)) * (w - padding * 2)
        const layerNodes = []
        for (let n = 0; n < maxShow; n++) {
          const y = padding + ((n + 0.5) / maxShow) * (h - padding * 2 - 20)
          layerNodes.push({ x, y, radius: l === 0 ? 2 : l === NN_LAYERS.length - 1 ? 6 : 3, layer: l })
        }
        nodes.push(layerNodes)
      }

      for (let l = 0; l < nodes.length - 1; l++) {
        const from = nodes[l]
        const to = nodes[l + 1]
        for (let i = 0; i < from.length; i++) {
          const connectCount = Math.min(to.length, 4)
          for (let j = 0; j < connectCount; j++) {
            const ti = Math.floor((j / connectCount) * to.length + Math.random() * 2) % to.length
            conns.push({ from: from[i], to: to[ti], weight: Math.random(), pulse: Math.random() })
          }
        }
      }

      nodesRef.current = nodes
      connsRef.current = conns
    }

    function draw(timestamp) {
      if (!running) return
      const result = setupCanvas()
      if (!result) { rafRef.current = requestAnimationFrame(draw); return }
      const { ctx, w, h } = result

      if (!nodesRef.current) initNet(w, h)
      const t = timestamp / 1000

      ctx.clearRect(0, 0, w, h)

      for (const conn of connsRef.current) {
        const alpha = 0.04 + conn.weight * 0.06
        ctx.strokeStyle = `rgba(0, 240, 255, ${alpha})`
        ctx.lineWidth = 0.5
        ctx.beginPath()
        ctx.moveTo(conn.from.x, conn.from.y)
        ctx.lineTo(conn.to.x, conn.to.y)
        ctx.stroke()

        conn.pulse += 0.003 + Math.random() * 0.002
        if (conn.pulse > 1) conn.pulse = 0
        if (conn.pulse > 0 && conn.pulse < 1) {
          const px = conn.from.x + (conn.to.x - conn.from.x) * conn.pulse
          const py = conn.from.y + (conn.to.y - conn.from.y) * conn.pulse
          const pa = Math.sin(conn.pulse * Math.PI) * 0.8
          ctx.fillStyle = `rgba(0, 240, 255, ${pa})`
          ctx.beginPath()
          ctx.arc(px, py, 1.5, 0, Math.PI * 2)
          ctx.fill()
        }
      }

      for (const layer of nodesRef.current) {
        for (const node of layer) {
          const activation = 0.3 + Math.sin(t * 2 + node.x * 0.05 + node.y * 0.03) * 0.3
          const a = 0.3 + activation * 0.7
          const isOutput = node.layer === NN_LAYERS.length - 1
          ctx.shadowColor = isOutput ? '#ff00ff' : '#00f0ff'
          ctx.shadowBlur = node.radius * 2
          ctx.fillStyle = isOutput ? `rgba(255, 0, 255, ${a})` : `rgba(0, 240, 255, ${a})`
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2)
          ctx.fill()
          ctx.shadowBlur = 0
        }
      }

      ctx.font = '8px -apple-system, sans-serif'
      ctx.fillStyle = 'rgba(255,255,255,0.2)'
      ctx.textAlign = 'center'
      for (let l = 0; l < nodesRef.current.length; l++) {
        if (nodesRef.current[l].length > 0) {
          ctx.fillText(LABELS[l], nodesRef.current[l][0].x, h - 5)
          ctx.fillText(String(NN_LAYERS[l]), nodesRef.current[l][0].x, h - 15)
        }
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)
    return () => {
      running = false
      cancelAnimationFrame(rafRef.current)
      nodesRef.current = null
      connsRef.current = null
    }
  }, [setupCanvas])

  return (
    <div className="panel neural-panel" style={{ gridColumn: 2, gridRow: 2 }}>
      <div className="panel-title">Neural Network &mdash; EdgeNet-v1</div>
      <div className="panel-body">
        <canvas ref={canvasRef} />
      </div>
      <div className="neural-overlay">
        <span>{neural?.model || 'EdgeNet-v1'}</span> / <span>{neural?.params || '2.4M'}</span> params<br />
        Epoch <span>{neural?.epoch || 0}</span> &middot; Loss <span>{neural?.loss?.toFixed(6) || '0.000000'}</span> &middot; <span>{neural?.inference_ms?.toFixed(1) || '0.0'}ms</span> inference
      </div>
    </div>
  )
}
