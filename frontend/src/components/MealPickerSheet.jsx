import { useState, useEffect } from 'react'
import { api } from '../api/client'
import useSwipeDismiss from '../hooks/useSwipeDismiss'

function daysAgo(dateStr) {
  if (!dateStr) return null
  const d = new Date(dateStr + 'T00:00:00')
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.floor((today - d) / (1000 * 60 * 60 * 24))
  if (diff === 0) return 'today'
  if (diff === 1) return 'yesterday'
  if (diff < 7) return `${diff} days ago`
  if (diff < 30) return `${Math.floor(diff / 7)} weeks ago`
  return `${Math.floor(diff / 30)}mo ago`
}

export default function MealPickerSheet({ date, dayName, onSelect, onFreeform, onClose }) {
  const [data, setData] = useState(null)
  const [history, setHistory] = useState(null)
  const [search, setSearch] = useState('')
  const [error, setError] = useState(false)
  const swipeHandlers = useSwipeDismiss(onClose)

  useEffect(() => {
    Promise.all([
      api.getCandidates(date),
      api.getMealHistory(),
    ]).then(([candidates, hist]) => {
      setData(candidates)
      setHistory(hist.history || [])
    }).catch(() => setError(true))
  }, [date])

  if (error) return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet" {...swipeHandlers} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="sheet-title">{dayName}</div>
        <div className="sheet-sub">Couldn't load recipes</div>
        <input
          className="picker-search"
          type="text"
          placeholder="Type a meal name..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && search.trim()) onFreeform(search.trim())
          }}
        />
        {search.trim() && (
          <button className="picker-option freeform" onClick={() => onFreeform(search.trim())}>
            Add "{search.trim()}" as a meal
          </button>
        )}
        <div style={{ marginTop: 12 }}>
          <button className="picker-option freeform" onClick={() => onFreeform('Eating Out')}>Eating Out</button>
        </div>
      </div>
    </div>
  )

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

  // Build favorites from history (cooked 2+ times, sorted by frequency)
  const favorites = history
    ? history.filter(h => h.cook_count >= 2).slice(0, 8)
    : []

  // Recipes the user hasn't cooked yet
  const historyIds = new Set((history || []).map(h => h.recipe_id))
  const otherRecipes = candidates.filter(r => !historyIds.has(r.id)).slice(0, 6)

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
              filtered.map(r => {
                const h = (history || []).find(x => x.recipe_id === r.id)
                return (
                  <button key={r.id} className="picker-option" onClick={() => onSelect(r.id)}>
                    {r.name}
                    {h && (
                      <span className="picker-favorite-meta">
                        Made {h.cook_count} time{h.cook_count !== 1 ? 's' : ''}, last {daysAgo(h.last_made)}
                      </span>
                    )}
                  </button>
                )
              })
            ) : (
              <button className="picker-option freeform" onClick={() => onFreeform(search.trim())}>
                Add "{search.trim()}" as a meal
              </button>
            )}
          </div>
        ) : (
          <>
            {/* Favorites */}
            {favorites.length > 0 && (
              <>
                <div className="picker-section-label">Your favorites</div>
                <div className="picker-pills" style={{ marginBottom: '16px' }}>
                  {favorites.map(f => (
                    <button
                      key={f.recipe_id}
                      className="meal-pill"
                      onClick={() => onSelect(f.recipe_id)}
                      title={`Made ${f.cook_count} times, last ${daysAgo(f.last_made)}`}
                    >
                      {f.recipe_name}
                    </button>
                  ))}
                </div>
              </>
            )}

            {/* Suggested / Other */}
            <div className="picker-section-label">
              {favorites.length > 0 ? 'Other recipes' : 'Suggested'}
            </div>
            <div className="picker-pills">
              {(favorites.length > 0 ? otherRecipes : candidates.slice(0, 8)).map(r => (
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
