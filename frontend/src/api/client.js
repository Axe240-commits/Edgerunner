const BASE = ''

export async function api(path, options = {}) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 8000)
  try {
    const res = await fetch(BASE + path, {
      credentials: 'include',
      signal: controller.signal,
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    })
    clearTimeout(timeout)
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }))
      throw new Error(err.error || res.statusText)
    }
    return await res.json()
  } catch (e) {
    clearTimeout(timeout)
    if (e.name === 'AbortError') return null
    console.error(`[API] ${path}:`, e.message)
    return null
  }
}

export function apiPost(path, body) {
  return api(path, { method: 'POST', body: JSON.stringify(body) })
}
