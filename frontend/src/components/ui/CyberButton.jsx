import './CyberButton.css'

export default function CyberButton({ children, onClick, variant = 'primary', type = 'button', disabled }) {
  return (
    <button
      className={`cyber-btn cyber-btn--${variant}`}
      onClick={onClick}
      type={type}
      disabled={disabled}
    >
      {children}
    </button>
  )
}
