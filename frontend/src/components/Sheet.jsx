import { useEffect } from 'react'
import useSwipeDismiss from '../hooks/useSwipeDismiss'
import useKeyboardInset from '../hooks/useKeyboardInset'

export default function Sheet({ onClose, className, children }) {
  const swipeHandlers = useSwipeDismiss(onClose)
  const kbInset = useKeyboardInset()

  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  const overlayStyle = kbInset > 0 ? { '--kb-inset': `${kbInset}px` } : undefined

  return (
    <div
      className="sheet-overlay"
      style={overlayStyle}
      onPointerDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className={`sheet${className ? ` ${className}` : ''}`}
        {...swipeHandlers}
      >
        <div className="sheet-handle" />
        <button className="sheet-close" onClick={onClose}>{'\u00D7'}</button>
        {children}
      </div>
    </div>
  )
}
