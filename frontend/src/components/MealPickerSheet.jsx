import { useState, useEffect } from 'react'
import { api } from '../api/client'
import useSwipeDismiss from '../hooks/useSwipeDismiss'

export default function MealPickerSheet({ date, dayName, onSelect, onFreeform, onClose }) {
  const [data, setData] = useState(null)
  const [search, setSearch] = useState('')
  const swipeHandlers = useSwipeDismiss(onClose)

  useEffect(() => {
    api.getCandidates(date).then(setData)
  }, [date])

  if (!data) return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet" {...swipeHandlers} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="loading">Flipping through recipes...</div>
      </div>
    </div>
  )

  const { candidates, all_recipes } = data
  const query = search.trim().toLowerCase()

  const filtered = query
    ? all_recipes.filter(r => r.name.toLowerCase().includes(query))
    : []

  const recent = candidates.slice(0, 8)

  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet meal-picker-sheet" {...swipeHandlers} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="sheet-title">{dayName}</div>
        <div className="sheet-sub">What are you making?</div>
        <input
          className="picker-search"
          type="text"
          placeholder="Search or type a meal..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && search.trim()) {
              if (filtered.length > 0) {
                onSelect(filtered[0].id)
              } else {
                onFreeform(search.trim())
              }
            }
          }}
        />

        {query ? (
          <div className="picker-results">
            {filtered.length > 0 ? (
              filtered.map(r => (
                <button key={r.id} className="picker-option" onClick={() => onSelect(r.id)}>
                  {r.name}
                </button>
              ))
            ) : (
              <button className="picker-option freeform" onClick={() => onFreeform(search.trim())}>
                Add "{search.trim()}" as a meal
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="picker-section-label">Suggested</div>
            <div className="picker-pills">
              {recent.map(r => (
                <button key={r.id} className="meal-pill" onClick={() => onSelect(r.id)}>
                  {r.name}
                </button>
              ))}
              <button
                className="meal-pill eating-out"
                onClick={() => onFreeform('Eating Out')}
              >
                Eating Out
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
