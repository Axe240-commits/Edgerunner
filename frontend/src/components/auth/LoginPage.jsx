import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import useAuth from '../../hooks/useAuth'
import CyberButton from '../ui/CyberButton'
import './AuthPage.css'

export default function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    const result = await login(username, password)
    setLoading(false)
    if (result.ok) {
      navigate('/dashboard')
    } else {
      setError(result.error)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card panel">
        <h2 className="auth-title">
          <span className="logo-edge">EDGE</span>
          <span className="logo-runner">RUNNER</span>
        </h2>
        <p className="auth-subtitle">LOGIN</p>
        <form onSubmit={handleSubmit} className="auth-form">
          <div className="auth-field">
            <label>USERNAME</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </div>
          <div className="auth-field">
            <label>PASSWORD</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          {error && <div className="auth-error">{error}</div>}
          <CyberButton type="submit" disabled={loading}>
            {loading ? 'CONNECTING...' : 'ACCESS'}
          </CyberButton>
        </form>
        <p className="auth-link">
          No account? <Link to="/register">Register</Link>
        </p>
        <p className="auth-link">
          <Link to="/">Back to home</Link>
        </p>
      </div>
    </div>
  )
}
