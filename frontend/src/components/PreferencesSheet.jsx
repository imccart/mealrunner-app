import { useState, useEffect } from 'react'
import { api } from '../api/client'
import useSwipeDismiss from '../hooks/useSwipeDismiss'

function AccordionSection({ title, count, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="prefs-accordion">
      <button className="prefs-accordion-header" onClick={() => setOpen(!open)}>
        <span className="prefs-accordion-title">{title}</span>
        {count != null && <span className="prefs-accordion-count">{count}</span>}
        <span className="prefs-accordion-arrow">{open ? '\u25B4' : '\u25BE'}</span>
      </button>
      {open && <div className="prefs-accordion-body">{children}</div>}
    </div>
  )
}

export default function PreferencesSheet({ onClose }) {
  const [regulars, setRegulars] = useState(null)
  const [pantry, setPantry] = useState(null)
  const [stores, setStores] = useState(null)
  const [addRegularText, setAddRegularText] = useState('')
  const [addPantryText, setAddPantryText] = useState('')
  const [addStoreName, setAddStoreName] = useState('')
  const [addStoreMode, setAddStoreMode] = useState('in-person')
  const swipeHandlers = useSwipeDismiss(onClose)

  useEffect(() => {
    api.getRegulars().then(data => setRegulars(data.regulars))
    api.getPantry().then(data => setPantry(data.items))
    api.getStores().then(data => setStores(data.stores))
  }, [])

  const handleAddRegular = async (e) => {
    e.preventDefault()
    if (!addRegularText.trim()) return
    await api.addRegular(addRegularText.trim())
    setAddRegularText('')
    const data = await api.getRegulars()
    setRegulars(data.regulars)
  }

  const handleRemoveRegular = async (name) => {
    await api.removeRegular(name)
    const data = await api.getRegulars()
    setRegulars(data.regulars)
  }

  const handleAddPantry = async (e) => {
    e.preventDefault()
    if (!addPantryText.trim()) return
    await api.addPantryItem(addPantryText.trim())
    setAddPantryText('')
    const data = await api.getPantry()
    setPantry(data.items)
  }

  const handleRemovePantry = async (id) => {
    await api.removePantryItem(id)
    const data = await api.getPantry()
    setPantry(data.items)
  }

  const handleAddStore = async (e) => {
    e.preventDefault()
    if (!addStoreName.trim()) return
    const name = addStoreName.trim()
    // Generate unique key
    let key = name[0].toLowerCase()
    if (stores && stores.some(s => s.key === key)) {
      key = name.slice(0, 2).toLowerCase()
    }
    const result = await api.addStore(name, key, addStoreMode)
    if (result.ok) {
      setAddStoreName('')
      const data = await api.getStores()
      setStores(data.stores)
    }
  }

  const handleRemoveStore = async (key) => {
    await api.removeStore(key)
    const data = await api.getStores()
    setStores(data.stores)
  }

  // Group regulars by shopping_group
  const regularGroups = {}
  if (regulars) {
    for (const r of regulars) {
      const g = r.shopping_group || 'Other'
      if (!regularGroups[g]) regularGroups[g] = []
      regularGroups[g].push(r)
    }
  }

  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet prefs-sheet" {...swipeHandlers} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="sheet-title">Preferences</div>
        <div className="sheet-sub">Configurable any time</div>

        {/* Stores */}
        <AccordionSection title="Stores" count={stores?.length || 0} defaultOpen>
          {stores && stores.length > 0 && (
            <div className="prefs-list">
              {stores.map(s => (
                <div key={s.key} className="prefs-list-item">
                  <span className="prefs-list-name">{s.name}</span>
                  <span className="prefs-list-meta">{s.mode}</span>
                  <button className="prefs-remove" onClick={() => handleRemoveStore(s.key)}>{'\u00D7'}</button>
                </div>
              ))}
            </div>
          )}
          <form onSubmit={handleAddStore} className="prefs-add-row">
            <input
              className="prefs-add-input"
              type="text"
              placeholder="Store name..."
              value={addStoreName}
              onChange={(e) => setAddStoreName(e.target.value)}
            />
            <select
              className="prefs-add-select"
              value={addStoreMode}
              onChange={(e) => setAddStoreMode(e.target.value)}
            >
              <option value="in-person">In-person</option>
              <option value="pickup">Pickup</option>
              <option value="delivery">Delivery</option>
            </select>
            <button className="btn primary" type="submit">+</button>
          </form>
        </AccordionSection>

        {/* Regulars */}
        <AccordionSection
          title="Regulars"
          count={regulars?.length || 0}
        >
          <div className="prefs-section-hint">
            Items you consider buying every trip
          </div>
          {regulars && regulars.length > 0 && (
            <div className="prefs-list">
              {Object.keys(regularGroups).sort().map(group => (
                <div key={group}>
                  <div className="prefs-list-group">{group}</div>
                  {regularGroups[group].map(r => (
                    <div key={r.id} className="prefs-list-item">
                      <span className="prefs-list-name">{r.name}</span>
                      <button className="prefs-remove" onClick={() => handleRemoveRegular(r.name)}>{'\u00D7'}</button>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
          <form onSubmit={handleAddRegular} className="prefs-add-row">
            <input
              className="prefs-add-input"
              type="text"
              placeholder="Add a regular..."
              value={addRegularText}
              onChange={(e) => setAddRegularText(e.target.value)}
            />
            <button className="btn primary" type="submit">+</button>
          </form>
        </AccordionSection>

        {/* Pantry */}
        <AccordionSection
          title="Pantry"
          count={pantry?.length || 0}
        >
          <div className="prefs-section-hint">
            Stuff you usually have — only buy when you're running low
          </div>
          {pantry && pantry.length > 0 && (
            <div className="prefs-list">
              {pantry.map(p => (
                <div key={p.id} className="prefs-list-item">
                  <span className="prefs-list-name">{p.name}</span>
                  <button className="prefs-remove" onClick={() => handleRemovePantry(p.id)}>{'\u00D7'}</button>
                </div>
              ))}
            </div>
          )}
          <form onSubmit={handleAddPantry} className="prefs-add-row">
            <input
              className="prefs-add-input"
              type="text"
              placeholder="Add a pantry item..."
              value={addPantryText}
              onChange={(e) => setAddPantryText(e.target.value)}
            />
            <button className="btn primary" type="submit">+</button>
          </form>
        </AccordionSection>

        {/* Transparency */}
        <AccordionSection title="Transparency">
          <div className="prefs-list">
            <div className="prefs-list-item">
              <span className="prefs-list-name">NOVA processing scores</span>
              <span className="prefs-list-meta">On</span>
            </div>
            <div className="prefs-list-item">
              <span className="prefs-list-name">Brand ownership</span>
              <span className="prefs-list-meta">Coming soon</span>
            </div>
          </div>
        </AccordionSection>

        {/* Integrations */}
        <AccordionSection title="Integrations">
          <div className="prefs-list">
            <div className="prefs-list-item">
              <span className="prefs-list-name">Kroger</span>
              <span className="prefs-list-meta">Configure via CLI</span>
            </div>
            <div className="prefs-list-item">
              <span className="prefs-list-name">Google Sheets</span>
              <span className="prefs-list-meta">Configure via CLI</span>
            </div>
          </div>
        </AccordionSection>

        {/* Account */}
        <AccordionSection title="Account">
          <button className="prefs-logout" onClick={async () => {
            await api.logout()
            localStorage.removeItem('souschef_onboarded')
            localStorage.removeItem('souschef_welcomed')
            window.location.reload()
          }}>
            Sign out
          </button>
        </AccordionSection>

        {/* About */}
        <div className="prefs-about">
          <div className="brand-name">sous<em style={{ color: 'var(--accent)', fontStyle: 'italic' }}>chef</em></div>
          <div style={{ marginTop: '4px' }}>by Aletheia</div>
        </div>
      </div>
    </div>
  )
}
