import { useState, useRef, useEffect } from 'react'
import './ChatPanel.css'

// Longer timeout for AI calls (90s instead of default 8s)
async function chatPost(path, body) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 90000)
  try {
    const res = await fetch(path, {
      method: 'POST',
      credentials: 'include',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    clearTimeout(timeout)
    return await res.json()
  } catch (e) {
    clearTimeout(timeout)
    if (e.name === 'AbortError') return { error: 'Timeout — Edge hat zu lange gebraucht' }
    return { error: e.message }
  }
}

export default function ChatPanel({ metaTs, tf, lookback, criteria, getCandleAtOffset, onAction }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [collapsed, setCollapsed] = useState(true)
  const messagesEndRef = useRef(null)

  // Auto-scroll on new message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async (text) => {
    if (!text.trim() || loading) return

    const userMsg = { role: 'user', content: text.trim() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    const res = await chatPost('/api/chat/analyze', {
      message: text.trim(),
      meta_ts: parseInt(metaTs, 10) || 0,
      tf,
      lookback,
      criteria,
      history: messages.filter(m => m.role === 'user' || m.role === 'assistant'),
    })

    setLoading(false)

    if (res?.reply) {
      const newMsgs = []
      // Show pre-analysis as data block (first time only, or on "Analysiere")
      if (res.analysis && text.toLowerCase().includes('analysiere')) {
        newMsgs.push({
          role: 'data',
          content: res.analysis,
        })
      }
      newMsgs.push({
        role: 'assistant',
        content: res.reply,
        actions: res.actions || [],
        model: res.model,
      })
      setMessages(prev => [...prev, ...newMsgs])
    } else {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: res?.error || 'Fehler bei der Analyse',
        actions: [],
      }])
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  const handleAction = (action) => {
    if (onAction) onAction(action)
  }

  return (
    <div className={`chat-panel ${collapsed ? 'collapsed' : ''}`}>
      <button className="chat-toggle" onClick={() => setCollapsed(!collapsed)}>
        <span className="chat-toggle-icon">{collapsed ? '▶' : '▼'}</span>
        <span>EDGE AI</span>
        {messages.length > 0 && <span className="chat-toggle-count">{messages.length}</span>}
      </button>

      {!collapsed && (
        <>
          <div className="chat-messages">
            {messages.length === 0 && (
              <div className="chat-empty">
                Frag Edge nach der aktuellen Kerze, Features oder Criteria-Vorschlaegen.
              </div>
            )}
            {messages.map((msg, i) => (
              <div key={i} className={`chat-msg chat-msg--${msg.role}`}>
                {msg.role === 'data' ? (
                  <details className="chat-data-block" open>
                    <summary className="chat-data-header">PRE-ANALYSE (exakte Daten)</summary>
                    <pre className="chat-data-content">{msg.content}</pre>
                  </details>
                ) : (
                  <div className="chat-msg-content">{msg.content}</div>
                )}
                {msg.actions?.length > 0 && (
                  <div className="chat-actions">
                    {msg.actions.map((a, j) => (
                      <button key={j} className="chat-action-chip" onClick={() => handleAction(a)}>
                        {a.action === 'toggle'
                          ? `+ ${a.feature} ${a.op} ${a.op === 'range' ? `${a.min}-${a.max}` : a.value}`
                          : `- ${a.feature}`}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {loading && (
              <div className="chat-msg chat-msg--assistant">
                <div className="chat-msg-content chat-loading">Edge denkt nach...</div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <div className="chat-quick-bar">
            <button className="chat-quick-btn"
              onClick={() => sendMessage('Analysiere diese Kerze')}
              disabled={loading}>
              Analysiere diese Kerze
            </button>
            <button className="chat-quick-btn"
              onClick={() => sendMessage('Welche Criteria wuerdest du setzen?')}
              disabled={loading}>
              Criteria vorschlagen
            </button>
          </div>

          <div className="chat-input-row">
            <input
              type="text"
              className="chat-input"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Frage an Edge..."
              disabled={loading}
            />
            <button className="chat-send-btn" onClick={() => sendMessage(input)}
              disabled={loading || !input.trim()}>
              Send
            </button>
          </div>
        </>
      )}
    </div>
  )
}
