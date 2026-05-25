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

  if (error) return <div className={styles.error}>{error}</div>
  if (!m) return <div className={styles.empty}>Loading…</div>

  const groups = [
    {
      title: 'Users',
      tiles: [
        { label: 'Total users', value: m.users_total },
        { label: 'New this week', value: m.users_new_7d },
        { label: 'Active (7d)', value: m.active_7d },
        { label: 'Active (30d)', value: m.active_30d },
        { label: 'Waitlist', value: m.waitlist },
      ],
    },
    {
      title: 'Engagement (last 7 days)',
      tiles: [
        { label: 'Kroger linked', value: m.kroger_linked },
        { label: 'Meals planned', value: m.meals_planned_7d, sub: `${m.meal_planners_7d} planners` },
        { label: 'Grocery items added', value: m.grocery_items_7d },
        { label: 'Receipts parsed', value: m.receipts_7d },
      ],
    },
    {
      title: 'Support & tips',
      tiles: [
        { label: 'Open feedback', value: m.open_feedback, alert: m.open_feedback > 0 },
        { label: 'Tip subscribers', value: m.tip_subscribers },
        { label: 'Tips received', value: m.tips_total },
        { label: 'Total collected', value: money(m.tips_cents), raw: true },
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
