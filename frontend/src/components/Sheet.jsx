import { useEffect } from 'react'
import useSwipeDismiss from '../hooks/useSwipeDismiss'

export default function Sheet({ onClose, className, children }) {
  const swipeHandlers = useSwipeDismiss(onClose)

  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div
        className={`sheet${className ? ` ${className}` : ''}`}
        {...swipeHandlers}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sheet-handle" />
        <button className="sheet-close" onClick={onClose}>{'\u00D7'}</button>
        {children}
      </div>
    </div>
  )
}
