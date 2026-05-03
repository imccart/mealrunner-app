import { useRef } from 'react'
import tipJarImg from '../assets/tip-jar.png'

// Mason jar tip-jar mark (DALL-E-generated PNG, processed via Pillow:
// transparent background, tight-cropped, square-padded, ICC stripped).
// Lives alongside runner-r.png as the project's other DALL-E asset.
//
// Shake animation lives on a wrapping <span>, not the <img>, so the
// transform-origin pivot is predictable across browsers.
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
        // Pivot at the bottom of the icon so the wobble looks like the jar
        // rocking on a countertop, not spinning in place.
        transformOrigin: '50% 100%',
      }}
    >
      <img
        src={tipJarImg}
        width={size}
        height={size}
        alt=""
        onClick={handleClick}
        style={{
          cursor: onClick ? 'pointer' : 'default',
          flexShrink: 0,
          opacity: active ? 1 : 0.75,
          transition: 'opacity 0.2s ease',
          // Prevent native image-drag on desktop.
          userSelect: 'none',
          WebkitUserDrag: 'none',
        }}
        draggable={false}
      />
    </span>
  )
}
