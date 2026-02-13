import { useState, useEffect, useRef, useCallback } from 'react'

export default function usePolling(fetchFn, intervalMs = 2000) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const timerRef = useRef(null)
  const mountedRef = useRef(true)
  const busyRef = useRef(false)

  const poll = useCallback(async () => {
    if (busyRef.current) return
    busyRef.current = true
    try {
      const result = await fetchFn()
      if (mountedRef.current) {
        if (result !== null) setData(result)
        setError(null)
        setLoading(false)
      }
    } catch (e) {
      if (mountedRef.current) {
        setError(e)
        setLoading(false)
      }
    } finally {
      busyRef.current = false
    }
  }, [fetchFn])

  useEffect(() => {
    mountedRef.current = true
    poll()
    timerRef.current = setInterval(poll, intervalMs)
    return () => {
      mountedRef.current = false
      clearInterval(timerRef.current)
    }
  }, [poll, intervalMs])

  return { data, loading, error }
}
