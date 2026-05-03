import { useRef } from 'react'
import tipJarImg from '../assets/tip-jar.png'

// Mason jar tip-jar mark (DALL-E PNG, processed via Pillow: transparent
// background, tight-cropped, square-padded, ICC stripped). Painted through
// a CSS mask so the line color tracks `var(--accent)` exactly like
// BentSpoonIcon and ApronIcon — without the mask the PNG's hardcoded brown
// would be a near-but-not-quite match, and a theme/accent change later
// would leave the tip jar stuck on the old color.
//
// Shake animation lives on the outer wrapper span so the transform-origin
// pivot is predictable across browsers.
export default function TipJarIcon({ size = 24, active = false, onClick }) {
  const wrapperRef = useRef(null)

  function handleClick(e) {
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
        transformOrigin: '50% 100%',
      }}
    >
      <span
        onClick={handleClick}
        role={onClick ? 'button' : undefined}
        aria-label="Tip jar"
        style={{
          display: 'inline-block',
          width: size,
          height: size,
          flexShrink: 0,
          backgroundColor: 'var(--accent)',
          // The mask: PNG alpha drives where the accent color paints.
          // Both prefixed and unprefixed for Safari + everyone else.
          WebkitMaskImage: `url(${tipJarImg})`,
          maskImage: `url(${tipJarImg})`,
          WebkitMaskRepeat: 'no-repeat',
          maskRepeat: 'no-repeat',
          WebkitMaskSize: 'contain',
          maskSize: 'contain',
          WebkitMaskPosition: 'center',
          maskPosition: 'center',
          cursor: onClick ? 'pointer' : 'default',
          opacity: active ? 1 : 0.75,
          transition: 'opacity 0.2s ease',
          userSelect: 'none',
        }}
      />
    </span>
  )
}
