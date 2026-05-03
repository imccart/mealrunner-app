import { useRef } from 'react'

// Mason jar with a $ glyph — the tip-jar metaphor service-industry users
// recognize at a glance. Stroke-based body to match BentSpoonIcon's style;
// the $ is a fill-text glyph because at 22-24px a hand-drawn $ stroke gets
// muddy. Shakes on click like coins rattling — handles the recognition
// problem the static icon has at small sizes.
export default function TipJarIcon({ size = 24, active = false, onClick }) {
  const ref = useRef(null)

  function handleClick(e) {
    // Coin-rattle wobble. Web Animations API has no CSS class plumbing and
    // self-cancels if the user re-clicks mid-animation.
    if (ref.current && typeof ref.current.animate === 'function') {
      ref.current.animate(
        [
          { transform: 'rotate(0deg)' },
          { transform: 'rotate(-14deg)', offset: 0.18 },
          { transform: 'rotate(11deg)', offset: 0.36 },
          { transform: 'rotate(-7deg)', offset: 0.54 },
          { transform: 'rotate(4deg)', offset: 0.72 },
          { transform: 'rotate(0deg)' },
        ],
        { duration: 520, easing: 'ease-out' },
      )
    }
    onClick?.(e)
  }

  return (
    <svg
      ref={ref}
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
        // Pivot from the lid so the wobble looks like the jar swinging on
        // its base, not spinning in place.
        transformOrigin: '50% 90%',
      }}
    >
      {/* Lid disc */}
      <path d="M8 3 L16 3" />
      <path d="M8 3 L8 5" />
      <path d="M16 3 L16 5" />
      {/* Screw band — slightly wider than the lid, narrower than the body */}
      <path d="M6 5 L18 5" />
      <path d="M6 5 L6 7.5" />
      <path d="M18 5 L18 7.5" />
      <path d="M6 7.5 L18 7.5" />
      {/* Jar body — squatter (wider than it is tall) so it reads as mason jar
          rather than tall vase, with a rounded bottom that suggests glass curve. */}
      <path d="M4 7.5 L4 18 a2.5 2.5 0 0 0 2.5 2.5 L17.5 20.5 a2.5 2.5 0 0 0 2.5 -2.5 L20 7.5" />
      {/* $ glyph in upper jar — small enough to leave room for coins below.
          Filled, no stroke. system-ui keeps the shape consistent across platforms. */}
      <text
        x="12"
        y="13.5"
        textAnchor="middle"
        fontSize="7"
        fontFamily="system-ui, -apple-system, sans-serif"
        fontWeight="700"
        fill="var(--accent)"
        stroke="none"
      >
        $
      </text>
      {/* Coin layer at the bottom of the jar — two thin ellipses suggest a
          stack of coins resting in the bottom, viewed from the side. */}
      <ellipse cx="12" cy="17" rx="5" ry="0.6" />
      <ellipse cx="12" cy="18.5" rx="4.5" ry="0.6" />
    </svg>
  )
}
