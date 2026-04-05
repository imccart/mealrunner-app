import { useState, useEffect } from 'react'
import styles from './Toast.module.css'

export default function Toast() {
  const [message, setMessage] = useState(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    function handleToast(e) {
      setMessage(e.detail)
      setVisible(true)
    }
    window.addEventListener('mealrunner-toast', handleToast)
    return () => window.removeEventListener('mealrunner-toast', handleToast)
  }, [])

  useEffect(() => {
    if (!visible) return
    const timer = setTimeout(() => setVisible(false), 3500)
    return () => clearTimeout(timer)
  }, [visible, message])

  if (!visible) return null

  return (
    <div className={styles.toast}>{message}</div>
  )
}
