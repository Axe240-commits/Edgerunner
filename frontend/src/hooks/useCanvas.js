import { useRef, useEffect, useCallback } from 'react'

export default function useCanvas() {
  const canvasRef = useRef(null)
  const ctxRef = useRef(null)
  const sizeRef = useRef({ w: 0, h: 0 })

  const setupCanvas = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const parent = canvas.parentElement
    const dpr = window.devicePixelRatio || 1
    const w = parent.clientWidth
    const h = parent.clientHeight
    if (w === 0 || h === 0) return null

    // Only resize when dimensions actually change
    if (sizeRef.current.w !== w || sizeRef.current.h !== h) {
      sizeRef.current = { w, h }
      canvas.width = w * dpr
      canvas.height = h * dpr
      const ctx = canvas.getContext('2d')
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctxRef.current = ctx
    }

    const ctx = ctxRef.current || canvas.getContext('2d')
    return { ctx, w, h, dpr }
  }, [])

  useEffect(() => {
    const onResize = () => {
      sizeRef.current = { w: 0, h: 0 }
      setupCanvas()
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [setupCanvas])

  return { canvasRef, ctxRef, setupCanvas }
}
