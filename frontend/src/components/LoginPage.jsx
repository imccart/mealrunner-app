import { useState, useEffect } from 'react'
import { api } from '../api/client'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState(null)

  // Check for expired magic link and clean up the URL
  const [expired] = useState(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('auth') === 'expired') {
      window.history.replaceState({}, '', window.location.pathname)
      return true
    }
    return false
  })

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!email.trim()) return
    setSending(true)
    setError(null)
    try {
      const result = await api.login(email.trim())
      if (result.ok) {
        setSent(true)
      } else {
        setError(result.error || 'Something went wrong')
      }
    } catch {
      setError('Could not reach the server')
    }
    setSending(false)
  }

  return (
    <div className="login">
      <div className="login-card">
        <img className="login-ladle" src="/ladle.png" alt="" />
        <div className="login-wordmark">sous<em>chef</em></div>

        {sent ? (
          <div className="login-sent">
            <div className="login-sent-icon">{'\u2709\uFE0F'}</div>
            <div className="login-sent-title">Check your inbox</div>
            <div className="login-sent-desc">
              If <strong>{email}</strong> is on our list, you'll get a sign-in link. Check your email and click it to continue.
            </div>
            <button
              className="login-resend"
              onClick={() => { setSent(false); setEmail('') }}
            >
              Use a different email
            </button>
          </div>
        ) : (
          <>
            <div className="login-desc">
              Sign in with your email to continue.
            </div>

            {expired && (
              <div className="login-error">That link has expired. Please request a new one.</div>
            )}
            {error && <div className="login-error">{error}</div>}

            <form onSubmit={handleSubmit} className="login-form">
              <input
                className="login-input"
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
                required
              />
              <button
                className="login-btn"
                type="submit"
                disabled={sending || !email.trim()}
              >
                {sending ? 'Sending...' : 'Send sign-in link'}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  )
}
