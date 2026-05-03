// Mason jar with a $ glyph — the tip-jar metaphor service-industry users
// recognize at a glance. Stroke-based body to match BentSpoonIcon's style;
// the $ is a fill-text glyph because at 22-24px a hand-drawn $ stroke gets
// muddy.
export default function TipJarIcon({ size = 24, active = false, onClick }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--accent)"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onClick={onClick}
      style={{
        cursor: onClick ? 'pointer' : 'default',
        flexShrink: 0,
        opacity: active ? 1 : 0.7,
        transition: 'opacity 0.2s ease',
      }}
    >
      {/* Lid disc */}
      <path d="M9 3.5 L15 3.5" />
      <path d="M9 3.5 L9 5.5" />
      <path d="M15 3.5 L15 5.5" />
      {/* Screw band — slightly wider than the lid */}
      <path d="M7.5 5.5 L16.5 5.5" />
      <path d="M7.5 5.5 L7.5 7.5" />
      <path d="M16.5 5.5 L16.5 7.5" />
      <path d="M7.5 7.5 L16.5 7.5" />
      {/* Jar body — rounded bottom corners */}
      <path d="M6.5 7.5 L6.5 19 a2 2 0 0 0 2 2 L15.5 21 a2 2 0 0 0 2 -2 L17.5 7.5" />
      {/* $ glyph filled, no stroke. system-ui keeps the shape consistent across platforms. */}
      <text
        x="12"
        y="17"
        textAnchor="middle"
        fontSize="8"
        fontFamily="system-ui, -apple-system, sans-serif"
        fontWeight="700"
        fill="var(--accent)"
        stroke="none"
      >
        $
      </text>
    </svg>
  )
}
