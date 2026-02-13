import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import useAuth from '../../hooks/useAuth'
import CyberButton from '../ui/CyberButton'
import './AuthPage.css'

export default function RegisterPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { register } = useAuth()
  const navigate = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    if (password !== confirm) {
      setError('Passwords do not match')
      return
    }
    if (password.length < 4) {
      setError('Password must be at least 4 characters')
      return
    }
    setLoading(true)
    const result = await register(username, password)
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
        <p className="auth-subtitle">REGISTER</p>
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
              autoComplete="new-password"
              required
            />
          </div>
          <div className="auth-field">
            <label>CONFIRM</label>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              required
            />
          </div>
          {error && <div className="auth-error">{error}</div>}
          <CyberButton type="submit" disabled={loading}>
            {loading ? 'INITIALIZING...' : 'CREATE'}
          </CyberButton>
        </form>
        <p className="auth-link">
          Already registered? <Link to="/login">Login</Link>
        </p>
        <p className="auth-link">
          <Link to="/">Back to home</Link>
        </p>
      </div>
    </div>
  )
}
