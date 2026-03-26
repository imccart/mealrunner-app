import { useState, useEffect } from 'react'
import { api } from '../api/client'
import Sheet from './Sheet'
import styles from './MealPickerSheet.module.css'

const MAX_SIDES = 3

export default function SidePickerSheet({ date, mealName, onSelect, onClose }) {
  const [data, setData] = useState(null)
  const [search, setSearch] = useState('')
  const [selectedSides, setSelectedSides] = useState([])

  useEffect(() => {
    api.getSides(date).then(d => {
      if (d.fixed) { onClose(); return }
      setData(d)
      // Initialize with currently selected sides
      const current = (d.sides || []).filter(s => s.current)
      setSelectedSides(current.map(s => ({ id: s.id, name: s.name })))
    })
  }, [date])

  if (!data) return (
    <Sheet onClose={onClose}>
      <div className="loading">Checking the sides...</div>
    </Sheet>
  )

  const toggleSide = (side) => {
    setSelectedSides(prev => {
      const exists = prev.find(s => s.id === side.id)
      if (exists) return prev.filter(s => s.id !== side.id)
      if (prev.length >= MAX_SIDES) return prev
      return [...prev, side]
    })
  }

  const confirm = () => {
    const sidesPayload = selectedSides.map(s => ({
      side_recipe_id: s.custom ? null : s.id,
      side_name: s.name,
    }))
    onSelect(sidesPayload)
  }

  const query = search.trim().toLowerCase()
  const filtered = query
    ? data.sides.filter(s => s.name.toLowerCase().includes(query))
    : data.sides

  const selectedIds = new Set(selectedSides.map(s => s.id))

  return (
    <Sheet onClose={onClose} className={styles.mealPickerSheet}>
      <div className="sheet-title">Side dishes</div>
      <div className="sheet-sub">Pick sides for {mealName} ({selectedSides.length}/{MAX_SIDES})</div>
      <input
        className={styles.pickerSearch}
        type="text"
        placeholder="Search or type a side..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {query && filtered.length === 0 ? (
        <div className={styles.pickerResults}>
          <button className={`${styles.pickerOption} ${styles.freeform}`} onClick={() => {
            if (selectedSides.length < MAX_SIDES) {
              const custom = { id: `custom-${search.trim()}`, name: search.trim(), custom: true }
              setSelectedSides(prev => [...prev, custom])
              setSearch('')
            }
          }}>
            Add "{search.trim()}" as a side
          </button>
        </div>
      ) : (
        <>
          {!query && (
            <div className={styles.pickerSectionLabel}>Options</div>
          )}
          <div className={styles.pickerPills}>
            {filtered.map(s => (
              <button
                key={s.id}
                className={`${styles.mealPill} ${selectedIds.has(s.id) ? styles.selectedSide : ''} ${s.in_use ? styles.inUse : ''}`}
                onClick={() => toggleSide(s)}
                disabled={!selectedIds.has(s.id) && selectedSides.length >= MAX_SIDES}
              >
                {s.name}
                {selectedIds.has(s.id) && ' \u2713'}
              </button>
            ))}
          </div>
        </>
      )}

      <div className={styles.pickerSideActions}>
        <button className="btn primary" onClick={confirm}>
          {selectedSides.length === 0 ? 'No sides' : `Done (${selectedSides.length})`}
        </button>
      </div>
    </Sheet>
  )
}
