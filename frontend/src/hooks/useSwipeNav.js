import { useRef, useCallback } from 'react'

/**
 * Horizontal swipe navigation between pages.
 * Returns touch handlers to attach to the main content area.
 * Swipe left → next page, swipe right → previous page.
 */
export default function useSwipeNav(pages, currentPage, setPage) {
  const startX = useRef(null)
  const startY = useRef(null)

  const onTouchStart = useCallback((e) => {
    startX.current = e.touches[0].clientX
    startY.current = e.touches[0].clientY
  }, [])

  const onTouchEnd = useCallback((e) => {
    if (startX.current === null) return
    const dx = e.changedTouches[0].clientX - startX.current
    const dy = e.changedTouches[0].clientY - startY.current
    startX.current = null
    startY.current = null

    // Require mostly horizontal swipe (2:1 ratio) and minimum 60px
    if (Math.abs(dx) < 60 || Math.abs(dy) > Math.abs(dx) * 0.5) return

    const idx = pages.indexOf(currentPage)
    if (idx === -1) return

    if (dx < 0 && idx < pages.length - 1) {
      setPage(pages[idx + 1])
    } else if (dx > 0 && idx > 0) {
      setPage(pages[idx - 1])
    }
  }, [pages, currentPage, setPage])

  return { onTouchStart, onTouchEnd }
}
