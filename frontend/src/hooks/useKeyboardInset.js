import { useEffect, useState } from 'react'

// Returns the height (px) of the on-screen keyboard, or 0 if it's closed.
// Uses the visualViewport API: when the keyboard opens, the visual viewport
// shrinks (without resizing the layout viewport) — the delta between the
// window height and the visual viewport's visible area is the keyboard.
//
// Consumed by Sheet.jsx so bottom-anchored sheets sit above the keyboard
// instead of being covered by it. Without this, position: fixed; bottom: 0
// stays at the bottom of the layout viewport and the keyboard overlays the
// lower half of the sheet.
export default function useKeyboardInset() {
  const [inset, setInset] = useState(0)

  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const update = () => {
      const next = Math.max(0, window.innerHeight - vv.height - vv.offsetTop)
      setInset(next)
    }
    update()
    vv.addEventListener('resize', update)
    vv.addEventListener('scroll', update)
    return () => {
      vv.removeEventListener('resize', update)
      vv.removeEventListener('scroll', update)
    }
  }, [])

  return inset
}
