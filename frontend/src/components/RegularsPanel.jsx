import { useState, useEffect } from 'react'
import { api } from '../api/client'

export default function RegularsPanel({ onChange }) {
  const [regulars, setRegulars] = useState(null)
  const [open, setOpen] = useState(false)
  const [addText, setAddText] = useState('')

  const load = async () => {
    const result = await api.getRegulars()
    setRegulars(result.regulars)
  }

  useEffect(() => { load() }, [])

  const handleToggle = async (id) => {
    const result = await api.toggleRegular(id)
    if (result.id) {
      setRegulars(prev => prev.map(r => r.id === id ? result : r))
      onChange?.()
    }
  }

  const handleAdd = async (e) => {
    e.preventDefault()
    if (!addText.trim()) return
    await api.addRegular(addText.trim())
    setAddText('')
    load()
    onChange?.()
  }

  if (!regulars) return null

  // Group by shopping_group
  const groups = {}
  for (const r of regulars) {
    const g = r.shopping_group || 'Other'
    if (!groups[g]) groups[g] = []
    groups[g].push(r)
  }

  const activeCount = regulars.filter(r => r.active).length

  return (
    <div className="regulars-panel">
      <div className="regulars-header" onClick={() => setOpen(!open)}>
        <h3>
          {open ? '\u25BC' : '\u25B6'} Add regulars
          {activeCount > 0 && ` (${activeCount})`}
        </h3>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          {open ? 'Close' : 'Tap to open'}
        </span>
      </div>

      {open && (
        <div className="regulars-list">
          {Object.keys(groups).sort().map(group => (
            <div key={group}>
              <div style={{
                padding: '6px 16px 2px',
                fontSize: '10px',
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                color: 'var(--text-muted)',
              }}>
                {group}
              </div>
              {groups[group].map(r => (
                <div
                  key={r.id}
                  className="regular-item"
                  onClick={() => handleToggle(r.id)}
                >
                  <div className={`regular-check ${r.active ? 'active' : ''}`}>
                    {r.active && '\u2713'}
                  </div>
                  <span className="regular-name">{r.name}</span>
                </div>
              ))}
            </div>
          ))}

          <form
            onSubmit={handleAdd}
            style={{ display: 'flex', gap: '8px', padding: '12px 16px 8px' }}
          >
            <input
              className="add-input"
              type="text"
              placeholder="Add a new regular..."
              value={addText}
              onChange={(e) => setAddText(e.target.value)}
            />
            <button className="btn sm" type="submit">Add</button>
          </form>
        </div>
      )}
    </div>
  )
}
