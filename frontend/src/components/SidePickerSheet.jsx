import { useState, useEffect } from 'react'
import { api } from '../api/client'
import useSwipeDismiss from '../hooks/useSwipeDismiss'

export default function SidePickerSheet({ date, mealName, onSelect, onClose }) {
  const [data, setData] = useState(null)
  const [search, setSearch] = useState('')
  const swipeHandlers = useSwipeDismiss(onClose)

  useEffect(() => {
    api.getSides(date).then(setData)
  }, [date])

  if (!data) return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet" {...swipeHandlers} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="loading">Checking the sides...</div>
      </div>
    </div>
  )

  if (data.fixed) {
    onClose()
    return null
  }

  const query = search.trim().toLowerCase()
  const filtered = query
    ? data.sides.filter(s => s.name.toLowerCase().includes(query))
    : data.sides

  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet meal-picker-sheet" {...swipeHandlers} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="sheet-title">Side dish</div>
        <div className="sheet-sub">Pick a side for {mealName}</div>
        <input
          className="picker-search"
          type="text"
          placeholder="Search or type a side..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && search.trim()) {
              onSelect(search.trim())
            }
          }}
        />

        {query && filtered.length === 0 ? (
          <div className="picker-results">
            <button className="picker-option freeform" onClick={() => onSelect(search.trim())}>
              Use "{search.trim()}" as a side
            </button>
          </div>
        ) : (
          <>
            {!query && (
              <div className="picker-section-label">Options</div>
            )}
            <div className="picker-pills">
              {filtered.map(s => (
                <button
                  key={s.name}
                  className={`meal-pill ${s.current ? 'current-side' : ''} ${s.in_use ? 'in-use' : ''}`}
                  onClick={() => onSelect(s.name)}
                >
                  {s.name}
                  {s.current && ' \u2713'}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
