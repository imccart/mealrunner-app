import { useState, useEffect } from 'react'
import styles from './OfflineBanner.module.css'

export default function OfflineBanner() {
  const [offline, setOffline] = useState(!navigator.onLine)

  useEffect(() => {
    const goOffline = () => setOffline(true)
    const goOnline = () => setOffline(false)
    window.addEventListener('offline', goOffline)
    window.addEventListener('online', goOnline)
    // Also listen for fetch failures as a fallback signal
    window.addEventListener('mealrunner-offline', goOffline)
    return () => {
      window.removeEventListener('offline', goOffline)
      window.removeEventListener('online', goOnline)
      window.removeEventListener('mealrunner-offline', goOffline)
    }
  }, [])

  if (!offline) return null

  return (
    <div className={styles.banner}>
      Offline — showing your last saved list
    </div>
  )
}
