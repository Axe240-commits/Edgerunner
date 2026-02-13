import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './theme/variables.css'
import './theme/global.css'
import './theme/panel.css'
import './theme/animations.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
