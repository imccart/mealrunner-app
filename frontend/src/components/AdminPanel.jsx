import { useState } from 'react'
import AdminStats from './AdminStats'
import AdminFeedback from './AdminFeedback'
import styles from './AdminFeedback.module.css'

export default function AdminPanel() {
  const [view, setView] = useState('stats')

  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <div className={styles.header}>
          <h1 className={styles.title}>Admin</h1>
          <button className={styles.exit} onClick={() => {
            history.replaceState(null, '', window.location.pathname + window.location.search)
            window.dispatchEvent(new HashChangeEvent('hashchange'))
          }}>
            Back to app
          </button>
        </div>

        <div className={styles.tabs}>
          <button
            className={`${styles.tab} ${view === 'stats' ? styles.active : ''}`}
            onClick={() => setView('stats')}
          >
            Stats
          </button>
          <button
            className={`${styles.tab} ${view === 'feedback' ? styles.active : ''}`}
            onClick={() => setView('feedback')}
          >
            Feedback
          </button>
        </div>

        {view === 'stats' ? <AdminStats /> : <AdminFeedback embedded />}
      </div>
    </div>
  )
}
