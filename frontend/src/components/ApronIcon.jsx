import { useRef } from 'react'

export default function ApronIcon({ size = 24, active = false, onClick }) {
  const wrapperRef = useRef(null)

  function handleClick(e) {
    // Sway from the hook — gentle fabric swing (small amplitude, longer
    // duration than the jar's wobble) that reads as cloth, not glass.
    if (wrapperRef.current && typeof wrapperRef.current.animate === 'function') {
      wrapperRef.current.animate(
        [
          { transform: 'rotate(0deg)' },
          { transform: 'rotate(-9deg)', offset: 0.22 },
          { transform: 'rotate(6deg)', offset: 0.48 },
          { transform: 'rotate(-3deg)', offset: 0.72 },
          { transform: 'rotate(0deg)' },
        ],
        { duration: 780, easing: 'ease-out' },
      )
    }
    onClick?.(e)
  }

  return (
  <span
    ref={wrapperRef}
    style={{
      display: 'inline-flex',
      // Pivot from the top — apron hanging on a hook physics, opposite of
      // the jar (which pivots from its base on a counter).
      transformOrigin: '50% 0%',
    }}
  >
    <svg
      width={size}
      height={size}
      viewBox="100 100 1000 1000"
      fill="var(--accent)"
      onClick={handleClick}
      style={{
        cursor: onClick ? 'pointer' : 'default',
        flexShrink: 0,
        opacity: active ? 1 : 0.7,
        transition: 'opacity 0.2s ease',
      }}
    >
      <path d="m742.78 721.22h-285.56c-6 0-12 4.7812-12 12v163.18c0 34.781 28.781 63.609 63.609 63.609h182.39c34.781 0 63.609-28.781 63.609-63.609v-163.18c-0.046874-6-4.8281-12-12.047-12zm-10.781 175.18c0 22.781-18 40.781-40.781 40.781l-182.44 0.046874c-22.781 0-40.781-18-40.781-40.781v-86.391l264-0.046876zm0-110.39h-264v-40.781h264z"/>
      <path d="m957.61 310.78c-28.781-38.391-72-49.219-114-60-10.781-2.3906-22.781-6-32.391-8.3906-28.781-8.3906-56.391-21.609-76.781-46.781-14.391-18-24-39.609-28.781-64.781-1.2188-6-7.2188-10.781-13.219-9.6094-6 1.2188-10.781 7.2188-9.6094 13.219 4.7812 28.781 16.781 55.219 33.609 75.609 4.7812 6 10.781 12 16.781 16.781v140.39h-265.22v-140.44c6-4.7812 10.781-10.781 16.781-16.781 16.781-20.391 27.609-46.781 33.609-75.609 1.2188-6-3.6094-12-9.6094-13.219s-12 3.6094-13.219 9.6094c-4.7812 25.219-14.391 46.781-28.781 64.781-20.391 26.391-48 38.391-76.781 46.781-10.781 3.6094-21.609 6-32.391 8.3906-42 10.781-85.219 21.609-114 60-19.219 26.391-27.609 54-27.609 100.78v361.22c0 6 4.7812 12 12 12s12-4.7812 12-12v-361.22c0-48 8.3906-67.219 22.781-86.391 24-32.391 61.219-40.781 100.78-51.609 7.2188-1.2188 13.219-3.6094 20.391-4.7812v106.78l-47.953 70.875v1.2188c0 1.2188-1.2188 1.2188-1.2188 2.3906v618c0 6 4.7812 12 12 12h507.61c6 0 12-4.7812 12-12v-619.22c0-1.2188 0-1.2188-1.2188-2.3906v-1.2188l-45.609-70.781v-106.78c7.2188 2.3906 13.219 3.6094 20.391 4.7812 39.609 9.6094 76.781 19.219 100.78 51.609 14.391 19.219 22.781 39.609 22.781 86.391v361.22c0 6 4.7812 12 12 12s12-4.7812 12-12v-361.22c-3.5156-45.609-10.734-73.172-29.906-99.609zm-115.22 145.22v600h-484.78v-600l45.609-70.781v-1.2188c0-1.2188 1.2188-1.2188 1.2188-2.3906v-121.22c13.219-4.7812 26.391-9.6094 39.609-18v135.61c0 6 4.7812 12 12 12h286.78c6 0 12-4.7812 12-12l-0.046875-134.39c12 7.2188 25.219 13.219 39.609 18v121.22c0 1.2188 0 1.2188 1.2188 2.3906v1.2188z"/>
    </svg>
  </span>
  )
}
