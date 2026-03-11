import { useState, useEffect } from 'react'
import { api } from '../api/client'

export default function CandidatesPanel({ date, onSelect, onClose }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    api.getCandidates(date).then(setData)
  }, [date])

  if (!data) return <div className="loading">Flipping through recipes...</div>

  const { candidates, all_recipes } = data

  return (
    <div className="candidates-panel">
      <div className="candidates-header">
        <span>Choose a meal</span>
        <button className="btn sm" onClick={onClose}>Close</button>
      </div>

      {candidates.length > 0 && (
        <>
          <div className="candidates-section-label">Suggested</div>
          {candidates.map(r => (
            <button
              key={r.id}
              className="candidate-option"
              onClick={() => onSelect(r.id)}
            >
              {r.name}
              <span className="candidate-meta">
                {r.cuisine} &middot; {r.effort} &middot; {r.prep_minutes + r.cook_minutes}min
              </span>
            </button>
          ))}
        </>
      )}

      <div className="candidates-section-label">All recipes</div>
      {all_recipes.map(r => (
        <button
          key={r.id}
          className="candidate-option"
          onClick={() => onSelect(r.id)}
        >
          {r.name}
          <span className="candidate-meta">
            {r.cuisine} &middot; {r.effort}
          </span>
        </button>
      ))}
    </div>
  )
}
