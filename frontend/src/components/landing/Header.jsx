import { Link } from 'react-router-dom'
import { fmt } from '../../lib/formatters'
import StatusDot from '../ui/StatusDot'
import CyberButton from '../ui/CyberButton'
import './Header.css'

export default function Header({ ticker }) {
  const price = ticker?.price || 0
  const change = ticker?.change_24h || 0

  return (
    <header className="landing-header panel">
      <div className="header-left">
        <h1 className="logo">
          <span className="logo-edge">EDGE</span>
          <span className="logo-runner">RUNNER</span>
        </h1>
        <StatusDot />
        <span className="badge">89 FEATURES</span>
      </div>

      <div className="header-center">
        <span className="price-value">${fmt(price, 2)}</span>
        <span className={`price-change ${change >= 0 ? 'up' : 'down'}`}>
          {change >= 0 ? '+' : ''}{change.toFixed(2)}%
        </span>
      </div>

      <div className="header-right">
        <Link to="/login">
          <CyberButton variant="ghost">Login</CyberButton>
        </Link>
        <Link to="/register">
          <CyberButton variant="gold">Register</CyberButton>
        </Link>
      </div>
    </header>
  )
}
