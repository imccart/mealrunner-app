import { useState, useEffect, useCallback } from 'react'
import { loadStripe } from '@stripe/stripe-js'
import { EmbeddedCheckoutProvider, EmbeddedCheckout } from '@stripe/react-stripe-js'
import Sheet from './Sheet'
import { api } from '../api/client'
import styles from './TipJarSheet.module.css'

// Memoize the Stripe promise across sheet open/close so we don't re-fetch
// the config and re-init Stripe.js on every interaction. Module-level cache
// is the pattern Stripe documents for React apps.
let _stripePromise = null
async function getStripePromise() {
  if (_stripePromise) return _stripePromise
  const config = await api.getStripeConfig()
  if (!config?.publishable_key) {
    throw new Error('Stripe publishable key missing')
  }
  _stripePromise = loadStripe(config.publishable_key)
  return _stripePromise
}

const ONE_TIME_PRESETS = [500, 1000]   // $5, $10 (custom adds a third option)
const MONTHLY_PRESETS = [500, 1000]    // $5, $10 only — Stripe needs Price objects

function fmtCents(cents) {
  if (cents == null) return ''
  if (cents % 100 === 0) return `$${cents / 100}`
  return `$${(cents / 100).toFixed(2)}`
}

function fmtDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return iso.slice(0, 10)
  }
}

export default function TipJarSheet({ onClose }) {
  const [mode, setMode] = useState('one_time')              // 'one_time' | 'monthly'
  const [presetCents, setPresetCents] = useState(500)       // selected preset, or null when using custom
  const [customDollars, setCustomDollars] = useState('')    // text input — kept as string for input control
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  // After submit:
  const [pendingSession, setPendingSession] = useState(null)  // {session_id, client_secret, fake}
  const [thanksAmount, setThanksAmount] = useState(null)      // cents — drives the thank-you state

  const [history, setHistory] = useState(null)              // {tips, active_subscription_id} | null
  const [historyLoading, setHistoryLoading] = useState(true)
  const [unavailable, setUnavailable] = useState(false)
  const [portalLoading, setPortalLoading] = useState(false)

  // Stripe.js promise — lazy-loaded only when a real (non-fake) checkout
  // session is created. Avoids the ~200KB stripe-js download for users who
  // open the sheet but don't proceed.
  const [stripePromise, setStripePromise] = useState(null)
  const [stripeLoadError, setStripeLoadError] = useState(false)

  const refreshHistory = useCallback(() => {
    setHistoryLoading(true)
    api.getTipHistory()
      .then(d => {
        setHistory(d)
        setHistoryLoading(false)
        setUnavailable(false)
      })
      .catch((err) => {
        setHistoryLoading(false)
        // 503 = tipping not configured (no Stripe creds, no test secret)
        if (String(err.message || '').startsWith('503')) {
          setUnavailable(true)
        }
      })
  }, [])

  useEffect(() => {
    refreshHistory()
  }, [refreshHistory])

  // When a real (non-fake) session is created, kick off Stripe.js load.
  // Done lazily so the bundle only downloads when the user actually proceeds.
  useEffect(() => {
    if (pendingSession && !pendingSession.fake && !stripePromise) {
      getStripePromise()
        .then(setStripePromise)
        .catch(() => setStripeLoadError(true))
    }
  }, [pendingSession, stripePromise])

  // When the user switches tabs, default to $5 and clear custom input.
  // Custom is only valid on one-time.
  function setModeReset(nextMode) {
    setMode(nextMode)
    setPresetCents(500)
    setCustomDollars('')
    setError('')
  }

  // Resolve which amount we'll actually charge.
  function resolveAmountCents() {
    if (mode === 'monthly' || presetCents != null) return presetCents
    // Custom one-time
    const dollars = parseFloat(customDollars)
    if (!Number.isFinite(dollars) || dollars <= 0) return null
    return Math.round(dollars * 100)
  }

  const amountCents = resolveAmountCents()
  const submittable = amountCents != null && amountCents >= 100 && amountCents <= 100000

  // Stripe iframe fires this when the payment flow completes. The webhook
  // is the source of truth for DB state; this handler just drives the UI
  // transition. Declared after `amountCents` so the deps array is in scope.
  const handleEmbeddedComplete = useCallback(() => {
    setThanksAmount(amountCents)
    setPendingSession(null)
    refreshHistory()
  }, [amountCents, refreshHistory])

  async function handleSubmit() {
    if (!submittable) {
      setError('Pick a preset or enter at least $1.')
      return
    }
    setError('')
    setSubmitting(true)
    try {
      const resp = await api.createTipCheckoutSession(mode, amountCents)
      setPendingSession({
        session_id: resp.session_id,
        client_secret: resp.client_secret,
        fake: !!resp.fake,
      })
    } catch (err) {
      const msg = String(err.message || '')
      if (msg.startsWith('503')) {
        setUnavailable(true)
      } else {
        setError("Something went wrong. Try again in a sec.")
      }
    } finally {
      setSubmitting(false)
    }
  }

  // Fake-mode flow: simulate a successful Stripe completion via the dev
  // endpoint. Real-mode flow will instead mount <EmbeddedCheckout> here once
  // a Stripe account exists; this branch goes away.
  async function handleFakeComplete() {
    if (!pendingSession) return
    setSubmitting(true)
    try {
      await api.devCompleteTipSession(pendingSession.session_id)
      setThanksAmount(amountCents)
      setPendingSession(null)
      refreshHistory()
    } catch (err) {
      setError("Couldn't simulate completion. Server may not be in test mode.")
    } finally {
      setSubmitting(false)
    }
  }

  async function handleManageSubscription() {
    setPortalLoading(true)
    try {
      const resp = await api.getTipPortalUrl()
      if (resp.url) {
        window.location.href = resp.url
      }
    } catch {
      setError("Couldn't open the management portal.")
    } finally {
      setPortalLoading(false)
    }
  }

  // ── Render ─────────────────────────────────────────────

  if (unavailable) {
    return (
      <Sheet onClose={onClose}>
        <div className={styles.tipSheet}>
          <div className={styles.tipHeader}>
            <h2 className={styles.tipTitle}>Tip jar</h2>
          </div>
          <div className={styles.tipUnavailable}>
            Tipping isn't set up yet. Check back soon.
          </div>
        </div>
      </Sheet>
    )
  }

  // Thank-you state — shown after a successful tip until the user closes the sheet.
  if (thanksAmount != null) {
    const wasMonthly = mode === 'monthly'
    return (
      <Sheet onClose={onClose}>
        <div className={styles.tipSheet}>
          <div className={styles.tipThanks}>
            <h2 className={styles.tipThanksTitle}>Thank you 🍝</h2>
            <p className={styles.tipThanksBody}>
              {wasMonthly
                ? `${fmtCents(thanksAmount)}/mo — really appreciated.`
                : `${fmtCents(thanksAmount)} received. Genuinely means a lot.`}
            </p>
            <button className={styles.tipManageBtn} onClick={onClose}>Back to MealRunner</button>
          </div>
        </div>
      </Sheet>
    )
  }

  // Pending session — fake mode shows simulate button; real mode would show iframe.
  if (pendingSession) {
    return (
      <Sheet onClose={onClose}>
        <div className={styles.tipSheet}>
          <div className={styles.tipHeader}>
            <h2 className={styles.tipTitle}>Tip jar</h2>
          </div>
          {pendingSession.fake ? (
            <div className={styles.tipFakeBox}>
              <p className={styles.tipFakeBoxTitle}>Test mode</p>
              <p className={styles.tipFakeBoxBody}>
                Stripe isn't configured on this environment, so this skips the
                payment iframe. Click below to simulate a successful tip and
                see what the post-completion state looks like.
              </p>
              <button
                className={styles.tipFakeCompleteBtn}
                onClick={handleFakeComplete}
                disabled={submitting}
                data-testid="tip-fake-complete"
              >
                {submitting ? 'Working...' : `Simulate $${(amountCents / 100).toFixed(2)} ${mode === 'monthly' ? '/mo' : 'tip'}`}
              </button>
            </div>
          ) : stripeLoadError ? (
            <div className={styles.tipFakeBox}>
              <p className={styles.tipFakeBoxBody}>
                Couldn't load the payment form. Try again in a moment.
              </p>
            </div>
          ) : !stripePromise ? (
            <div className={styles.tipFakeBox}>
              <p className={styles.tipFakeBoxBody}>Loading payment form...</p>
            </div>
          ) : (
            <div className={styles.tipCheckout} data-testid="tip-embedded-checkout">
              <EmbeddedCheckoutProvider
                stripe={stripePromise}
                options={{
                  clientSecret: pendingSession.client_secret,
                  onComplete: handleEmbeddedComplete,
                }}
              >
                <EmbeddedCheckout />
              </EmbeddedCheckoutProvider>
            </div>
          )}
          {error && <p className={styles.tipError}>{error}</p>}
        </div>
      </Sheet>
    )
  }

  const presets = mode === 'one_time' ? ONE_TIME_PRESETS : MONTHLY_PRESETS
  const activeSub = history?.active_subscription_id || null

  return (
    <Sheet onClose={onClose}>
      <div className={styles.tipSheet}>
        <div className={styles.tipHeader}>
          <h2 className={styles.tipTitle}>Tip jar</h2>
          <p className={styles.tipSubtitle}>
            Be sure to tip your app maker.
          </p>
        </div>

        {activeSub && (
          <div className={styles.tipActiveBox}>
            <div className={styles.tipActiveLabel}>
              You're already tipping monthly. <strong>Thank you.</strong>
            </div>
            <button
              className={styles.tipManageBtn}
              onClick={handleManageSubscription}
              disabled={portalLoading}
              data-testid="tip-manage-subscription"
            >
              {portalLoading ? '...' : 'Manage'}
            </button>
          </div>
        )}

        <div className={styles.tipModeToggle} role="tablist">
          <button
            role="tab"
            className={`${styles.tipModeBtn} ${mode === 'one_time' ? styles.active : ''}`}
            onClick={() => setModeReset('one_time')}
            data-testid="tip-mode-one_time"
          >
            One time
          </button>
          <button
            role="tab"
            className={`${styles.tipModeBtn} ${mode === 'monthly' ? styles.active : ''}`}
            onClick={() => setModeReset('monthly')}
            data-testid="tip-mode-monthly"
          >
            Monthly
          </button>
        </div>

        <div className={`${styles.tipPresets} ${mode === 'monthly' ? styles.twoCol : ''}`}>
          {presets.map(cents => (
            <button
              key={cents}
              type="button"
              className={`${styles.tipPresetBtn} ${presetCents === cents ? styles.selected : ''}`}
              onClick={() => { setPresetCents(cents); setCustomDollars(''); setError('') }}
              data-testid={`tip-preset-${cents}`}
            >
              ${cents / 100}{mode === 'monthly' ? '/mo' : ''}
            </button>
          ))}
          {mode === 'one_time' && (
            <button
              type="button"
              className={`${styles.tipPresetBtn} ${presetCents == null ? styles.selected : ''}`}
              onClick={() => { setPresetCents(null); setError('') }}
              data-testid="tip-preset-custom"
            >
              Other
            </button>
          )}
        </div>

        {mode === 'one_time' && presetCents == null && (
          <div className={styles.tipCustomWrap}>
            <label className={styles.tipCustomLabel} htmlFor="tip-custom-input">Amount</label>
            <span className={styles.tipCustomDollar}>$</span>
            <input
              id="tip-custom-input"
              data-testid="tip-custom-input"
              className={styles.tipCustomInput}
              type="number"
              inputMode="decimal"
              min="1"
              step="0.01"
              placeholder="0.00"
              value={customDollars}
              onChange={(e) => { setCustomDollars(e.target.value); setError('') }}
            />
          </div>
        )}

        <button
          className={styles.tipSubmit}
          onClick={handleSubmit}
          disabled={!submittable || submitting}
          data-testid="tip-submit"
        >
          {submitting
            ? 'Working...'
            : mode === 'monthly'
              ? `Tip ${fmtCents(amountCents)}/mo`
              : amountCents
                ? `Leave a ${fmtCents(amountCents)} tip`
                : 'Leave a tip'}
        </button>
        {error && <p className={styles.tipError}>{error}</p>}

        {!historyLoading && history?.tips?.length > 0 && (
          <div className={styles.tipHistory}>
            <p className={styles.tipHistoryTitle}>Your tips</p>
            {history.tips.slice(0, 10).map(t => (
              <div key={t.id} className={styles.tipHistoryItem} data-testid="tip-history-row">
                <span>
                  {fmtCents(t.amount_cents)}
                  {t.is_recurring ? ' /mo' : ''}
                </span>
                <span className={styles.tipHistoryItemDate}>{fmtDate(t.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Sheet>
  )
}
