import { useRef } from 'react'

// Mason jar with a coin slot in the lid + a prominent $ in the body —
// the lid slot is the single most recognizable "you put money in here" cue;
// pairs with the wide squat jar shape to read as a tip jar at small sizes.
//
// Shake on click is wrapped on a <span> rather than the <svg> because
// CSS `transform-origin` on SVG root elements is finicky across browsers;
// HTML elements behave consistently.
export default function TipJarIcon({ size = 24, active = false, onClick }) {
  const wrapperRef = useRef(null)

  function handleClick(e) {
    // Coin-rattle wobble — wide swings so the motion is perceptible even
    // as the bottom-sheet animates in over the top of the icon.
    if (wrapperRef.current && typeof wrapperRef.current.animate === 'function') {
      wrapperRef.current.animate(
        [
          { transform: 'rotate(0deg)' },
          { transform: 'rotate(-26deg)', offset: 0.12 },
          { transform: 'rotate(22deg)', offset: 0.28 },
          { transform: 'rotate(-16deg)', offset: 0.45 },
          { transform: 'rotate(11deg)', offset: 0.62 },
          { transform: 'rotate(-5deg)', offset: 0.80 },
          { transform: 'rotate(0deg)' },
        ],
        { duration: 680, easing: 'ease-out' },
      )
    }
    onClick?.(e)
  }

  return (
    <span
      ref={wrapperRef}
      style={{
        display: 'inline-flex',
        // Pivot from just below the jar base so the wobble looks like the
        // jar swaying on a countertop, not spinning in place.
        transformOrigin: '50% 95%',
      }}
    >
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        onClick={handleClick}
        style={{
          cursor: onClick ? 'pointer' : 'default',
          flexShrink: 0,
          opacity: active ? 1 : 0.7,
          transition: 'opacity 0.2s ease',
        }}
      >
        {/* Lid disc */}
        <path d="M8 3 L16 3" />
        <path d="M8 3 L8 5" />
        <path d="M16 3 L16 5" />
        {/* Coin slot — a thicker stroke in the center of the lid is the
            piggy-bank cue people read instantly as "deposit here". */}
        <path d="M10.5 4 L13.5 4" strokeWidth="1.8" />
        {/* Screw band */}
        <path d="M6 5 L18 5" />
        <path d="M6 5 L6 7.5" />
        <path d="M18 5 L18 7.5" />
        <path d="M6 7.5 L18 7.5" />
        {/* Jar body — wider than tall so it reads as a squat mason jar. */}
        <path d="M4 7.5 L4 18 a2.5 2.5 0 0 0 2.5 2.5 L17.5 20.5 a2.5 2.5 0 0 0 2.5 -2.5 L20 7.5" />
        {/* $ glyph dominates the body interior. system-ui keeps the shape
            consistent across platforms; bolder weight reads better at 22px. */}
        <text
          x="12"
          y="16.5"
          textAnchor="middle"
          fontSize="10"
          fontFamily="system-ui, -apple-system, sans-serif"
          fontWeight="700"
          fill="var(--accent)"
          stroke="none"
        >
          $
        </text>
      </svg>
    </span>
  )
}
