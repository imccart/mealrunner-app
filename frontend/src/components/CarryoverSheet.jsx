import { useState } from 'react'
import Sheet from './Sheet'

export default function CarryoverSheet({ items, onConfirm, onSkip }) {
  const [selected, setSelected] = useState(new Set(items.map(i => i.name)))

  const toggle = (name) => {
    const next = new Set(selected)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    setSelected(next)
  }

  return (
    <Sheet onClose={onSkip}>
      <div className="sheet-title">Still need these?</div>
      <div className="sheet-sub">
        {items.length} item{items.length !== 1 ? 's' : ''} weren't checked off last trip
      </div>
      <div className="carry-items">
        {items.map(item => (
          <div key={item.name} className="carry-item" onClick={() => toggle(item.name)}>
            <div className={`carry-check ${selected.has(item.name) ? 'active' : ''}`}>
              {selected.has(item.name) ? '\u2713' : ''}
            </div>
            <span>{item.name}</span>
          </div>
        ))}
      </div>
      <div className="sheet-btn-row">
        <button className="sheet-btn-secondary" onClick={onSkip}>Skip</button>
        <button
          className="sheet-btn-primary"
          onClick={() => onConfirm([...selected])}
        >
          Carry over {'\u2192'}
        </button>
      </div>
    </Sheet>
  )
}
