import './Footer.css'

export default function Footer() {
  return (
    <footer className="landing-footer">
      <div className="footer-links">
        <a href="#privacy">Privacy</a>
        <span className="footer-sep">|</span>
        <a href="#terms">Terms</a>
        <span className="footer-sep">|</span>
        <a href="#imprint">Imprint</a>
      </div>
      <div className="footer-copy">
        EDGERUNNER &copy; {new Date().getFullYear()} &mdash; BTC Signal Analyzer
      </div>
    </footer>
  )
}
