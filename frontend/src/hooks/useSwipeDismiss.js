import { useRef, useCallback } from 'react'

/**
 * Returns touch event handlers for a bottom sheet element.
 * Swipe down past threshold dismisses the sheet via onClose.
 * Also applies a visual translateY while dragging.
 */
export default function useSwipeDismiss(onClose, threshold = 80) {
  const startY = useRef(null)
  const sheetEl = useRef(null)

  const onTouchStart = useCallback((e) => {
    // Only track swipe from the top area (handle + a bit below)
    const touch = e.touches[0]
    startY.current = touch.clientY
    sheetEl.current = e.currentTarget
  }, [])

  const onTouchMove = useCallback((e) => {
    if (startY.current === null) return
    const dy = e.touches[0].clientY - startY.current
    if (dy > 0 && sheetEl.current) {
      sheetEl.current.style.transform = `translateY(${dy}px)`
      sheetEl.current.style.transition = 'none'
    }
  }, [])

  const onTouchEnd = useCallback((e) => {
    if (startY.current === null) return
    const dy = e.changedTouches[0].clientY - startY.current
    if (sheetEl.current) {
      sheetEl.current.style.transition = 'transform 0.2s ease-out'
      sheetEl.current.style.transform = ''
    }
    if (dy > threshold) {
      onClose()
    }
    startY.current = null
    sheetEl.current = null
  }, [onClose, threshold])

  return { onTouchStart, onTouchMove, onTouchEnd }
}
