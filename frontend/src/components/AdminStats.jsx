import { useEffect, useState } from 'react'
import { api } from '../api/client'
import styles from './AdminStats.module.css'

function money(cents) {
  return `$${((cents || 0) / 100).toFixed(2)}`
}

export default function AdminStats() {
  const [m, setM] = useState(null)
  const [error, setError] = useState(null)

  const load = async () => {
    try {
      const data = await api.getAdminMetrics()
      if (data?.error) {
        setError(data.error)
        return
      }
      setM(data.metrics || {})
      setError(null)
    } catch (e) {
      setError(e.message || 'Failed to load metrics')
    }
  }

  useEffect(() => { load() }, [])

  if (error) return (
    <div className={styles.error}>
      {error}
      <button className={styles.retry} onClick={() => { setM(null); setError(null); load() }}>Retry</button>
    </div>
  )
  if (!m) return <div className={styles.empty}>Loading…</div>

  // Each concept is a single tile; the breakdowns live in the subtitle so no
  // number is shown twice (total = active + not-logged-in, etc.).
  const usersSub = `${m.active_signed_in} active · ${m.pending_activation} not logged in`
    + (m.users_new_7d > 0 ? ` · ${m.users_new_7d} new this wk` : '')
  const tipsSub = money(m.tips_cents)
    + (m.tip_subscribers > 0 ? ` · ${m.tip_subscribers} monthly` : '')

  const groups = [
    {
      title: 'Overview',
      tiles: [
        { label: 'Users', value: m.users_total, sub: usersSub },
        { label: 'Households', value: m.households },
        { label: 'Waitlist', value: m.waitlist },
        { label: 'Invites sent', value: m.invites_sent, sub: `${m.invites_accepted ?? 0} accepted` },
      ],
    },
    {
      title: 'Engagement (last 7 days)',
      tiles: [
        { label: 'Kroger linked', value: m.kroger_linked },
        { label: 'Meals planned', value: m.meals_planned_7d },
        { label: 'Grocery items added', value: m.grocery_items_7d },
        { label: 'Receipts parsed', value: m.receipts_7d },
      ],
    },
    {
      title: 'Support & money',
      tiles: [
        { label: 'Open feedback', value: m.open_feedback, alert: m.open_feedback > 0 },
        { label: 'Tips received', value: m.tips_total, sub: tipsSub },
      ],
    },
  ]

  return (
    <div className={styles.wrap}>
      {groups.map(g => (
        <div key={g.title} className={styles.group}>
          <div className={styles.groupTitle}>{g.title}</div>
          <div className={styles.grid}>
            {g.tiles.map(t => (
              <div key={t.label} className={`${styles.tile} ${t.alert ? styles.alert : ''}`}>
                <div className={styles.value}>{t.raw ? t.value : (t.value ?? 0)}</div>
                <div className={styles.label}>{t.label}</div>
                {t.sub && <div className={styles.sub}>{t.sub}</div>}
              </div>
            ))}
          </div>
        </div>
      ))}
      <div className={styles.footer}>
        <button className={styles.refresh} onClick={load}>Refresh</button>
      </div>
    </div>
  )
}
