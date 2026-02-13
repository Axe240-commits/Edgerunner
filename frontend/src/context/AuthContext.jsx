import { createContext, useState, useEffect, useCallback } from 'react'
import { api, apiPost } from '../api/client'

export const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const checkAuth = useCallback(async () => {
    const data = await api('/api/auth/me')
    if (data && data.username) {
      setUser(data)
    } else {
      setUser(null)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  const login = async (username, password) => {
    const data = await apiPost('/api/auth/login', { username, password })
    if (data && data.username) {
      setUser(data)
      return { ok: true }
    }
    return { ok: false, error: data?.error || 'Login failed' }
  }

  const register = async (username, password) => {
    const data = await apiPost('/api/auth/register', { username, password })
    if (data && data.username) {
      setUser(data)
      return { ok: true }
    }
    return { ok: false, error: data?.error || 'Registration failed' }
  }

  const logout = async () => {
    await apiPost('/api/auth/logout', {})
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
