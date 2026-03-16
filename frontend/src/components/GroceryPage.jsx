import { useState, useEffect } from 'react'
import { api } from '../api/client'
import AutocompleteInput from './AutocompleteInput'
import Sheet from './Sheet'
import FeedbackFab from './FeedbackFab'

const GROUP_ORDER = [
  'Produce', 'Meat', 'Dairy & Eggs', 'Bread & Bakery',
  'Pasta & Grains', 'Spices & Baking', 'Condiments & Sauces',
  'Canned Goods', 'Frozen', 'Breakfast & Beverages', 'Snacks',
  'Personal Care', 'Household', 'Cleaning', 'Pets', 'Other'
]

export default function GroceryPage({ sidebar = false }) {
  const [grocery, setGrocery] = useState(null)
  const [meals, setMeals] = useState(null)
  const [addText, setAddText] = useState('')
  const [addDupe, setAddDupe] = useState(false)
  const [collapsedGroups, setCollapsedGroups] = useState({})
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [recatItem, setRecatItem] = useState(null)
  const [hideDone, setHideDone] = useState(false)

  // Inline prompt state
  const [regularsData, setRegularsData] = useState(null)
  const [regularsChecked, setRegularsChecked] = useState(new Set())
  const [regularsExpanded, setRegularsExpanded] = useState(false)
  const [pantryData, setPantryData] = useState(null)
  const [pantryChecked, setPantryChecked] = useState(new Set())
  const [pantryExpanded, setPantryExpanded] = useState(false)

  const load = async () => {
    try {
      const [g, m] = await Promise.all([api.getGrocery(), api.getMeals()])
      setGrocery(g)
      setMeals(m)
    } catch {
      setLoadError(true)
    }
    setLoading(false)
  }

  const [itemPool, setItemPool] = useState([])
  useEffect(() => {
    api.getGrocerySuggestions().then(data => {
      setItemPool(data.suggestions || [])
    }).catch(() => {})
  }, [])

  useEffect(() => { load() }, [])

  if (loading) return <><div className="loading">Gathering ingredients...</div><FeedbackFab page="grocery" /></>
  if (loadError) return <><div className="loading">Something went wrong loading your list. Try refreshing.</div><FeedbackFab page="grocery" /></>

  const { items_by_group, checked, ordered, skipped, start_date, end_date, regulars_added, pantry_checked } = grocery
  const checkedSet = new Set((checked || []).map(n => n.toLowerCase()))
  const orderedSet = new Set((ordered || []).map(n => n.toLowerCase()))
  const skippedSet = new Set((skipped || []).map(n => n.toLowerCase()))

  const onListSet = new Set()
  for (const group of Object.values(items_by_group)) {
    for (const item of group) {
      onListSet.add(item.name.toLowerCase())
    }
  }

  let totalItems = 0
  let doneCount = 0
  const groupCounts = {}
  for (const [group, items] of Object.entries(items_by_group)) {
    let groupRemaining = 0
    for (const item of items) {
      totalItems++
      const nameLower = item.name.toLowerCase()
      if (checkedSet.has(nameLower) || skippedSet.has(nameLower)) {
        doneCount++
      } else if (!orderedSet.has(nameLower)) {
        groupRemaining++
      }
    }
    groupCounts[group] = { total: items.length, remaining: groupRemaining }
  }
  const remainingCount = totalItems - doneCount

  const sortedGroups = Object.keys(items_by_group).sort((a, b) => {
    const ai = GROUP_ORDER.indexOf(a)
    const bi = GROUP_ORDER.indexOf(b)
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  })

  const hasItems = sortedGroups.length > 0

  const isGroupAllDone = (group) => {
    return groupCounts[group] && groupCounts[group].remaining === 0
  }

  const isGroupExpanded = (group) => {
    if (collapsedGroups[group] !== undefined) return !collapsedGroups[group]
    return !isGroupAllDone(group)
  }

  const handleGroupToggle = (group) => {
    const currentlyExpanded = isGroupExpanded(group)
    setCollapsedGroups(prev => ({ ...prev, [group]: currentlyExpanded }))
  }

  const handleToggle = async (name) => {
    const prev = grocery
    const newChecked = new Set(checkedSet)
    const newSkipped = new Set(skippedSet)
    if (newChecked.has(name.toLowerCase())) {
      newChecked.delete(name.toLowerCase())
    } else {
      newChecked.add(name.toLowerCase())
      newSkipped.delete(name.toLowerCase())
    }
    setGrocery({ ...grocery, checked: [...newChecked], skipped: [...newSkipped] })
    try {
      await api.toggleGroceryItem(name)
    } catch {
      setGrocery(prev)
    }
  }

  const handleSkip = async (name) => {
    const prev = grocery
    const newSkipped = new Set(skippedSet)
    const newChecked = new Set(checkedSet)
    if (newSkipped.has(name.toLowerCase())) {
      newSkipped.delete(name.toLowerCase())
      setGrocery({ ...grocery, skipped: [...newSkipped] })
      try {
        await api.unskipGroceryItem(name)
      } catch {
        setGrocery(prev)
      }
    } else {
      newSkipped.add(name.toLowerCase())
      newChecked.delete(name.toLowerCase())
      setGrocery({ ...grocery, skipped: [...newSkipped], checked: [...newChecked] })
      try {
        await api.skipGroceryItem(name)
      } catch {
        setGrocery(prev)
      }
    }
  }

  const handleRecategorize = async (group) => {
    if (!recatItem) return
    try {
      const result = await api.recategorizeItem(recatItem, group)
      setGrocery(result)
    } catch {
      // stay on current state
    }
    setRecatItem(null)
  }

  const handleAddSubmit = async (name) => {
    const trimmed = name.trim()
    if (!trimmed || addDupe) return
    try {
      const result = await api.addGroceryItem(trimmed)
      setGrocery(result)
      setAddText('')
      setAddDupe(false)
    } catch {
      // input stays so user can retry
    }
  }

  // Regulars prompt handlers
  const handleRegularsExpand = async () => {
    if (!regularsData) {
      try {
        const data = await api.getRegulars()
        const active = (data.regulars || []).filter(r => r.active)
        setRegularsData(active)
        setRegularsChecked(new Set(active.map(r => r.name)))
      } catch {
        setRegularsData([])
      }
    }
    setRegularsExpanded(true)
  }

  const handleRegularsSubmit = async () => {
    try {
      const result = await api.addRegulars([...regularsChecked])
      setGrocery(result)
    } catch {}
    setRegularsExpanded(false)
  }

  const handleRegularsDismiss = async () => {
    // Dismiss without adding — mark as handled with empty selection
    try {
      const result = await api.addRegulars([])
      setGrocery(result)
    } catch {}
    setRegularsExpanded(false)
  }

  // Pantry prompt handlers
  const handlePantryExpand = async () => {
    if (!pantryData) {
      try {
        const data = await api.getPantry()
        setPantryData(data.items || [])
      } catch {
        setPantryData([])
      }
    }
    setPantryExpanded(true)
  }

  const handlePantrySubmit = async () => {
    try {
      const result = await api.addPantryItems([...pantryChecked])
      setGrocery(result)
    } catch {}
    setPantryExpanded(false)
  }

  const handlePantryDismiss = async () => {
    try {
      const result = await api.addPantryItems([])
      setGrocery(result)
    } catch {}
    setPantryExpanded(false)
  }

  // Inline prompt cards
  const promptCards = (
    <>
      {!regulars_added && (
        <div className="grocery-prompt-card">
          {!regularsExpanded ? (
            <button className="grocery-prompt-trigger" onClick={handleRegularsExpand}>
              <span className="grocery-prompt-icon">{'\u{1F504}'}</span>
              <span>Add your regulars</span>
              <span className="grocery-prompt-arrow">{'\u203A'}</span>
            </button>
          ) : (
            <div className="grocery-prompt-body">
              <div className="grocery-prompt-title">Regulars</div>
              <div className="grocery-prompt-desc">Uncheck anything you don't need this time.</div>
              {regularsData && regularsData.length > 0 ? (
                <div className="grocery-prompt-checklist">
                  {regularsData.map(r => (
                    <div
                      key={r.id}
                      className="grocery-prompt-check-item"
                      onClick={() => {
                        setRegularsChecked(prev => {
                          const next = new Set(prev)
                          next.has(r.name) ? next.delete(r.name) : next.add(r.name)
                          return next
                        })
                      }}
                    >
                      <div className={`grocery-prompt-check ${regularsChecked.has(r.name) ? 'active' : ''}`}>
                        {regularsChecked.has(r.name) && '\u2713'}
                      </div>
                      <span>{r.name}</span>
                      {r.shopping_group && <span className="grocery-prompt-group">{r.shopping_group}</span>}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="grocery-prompt-empty">No active regulars yet.</div>
              )}
              <div className="grocery-prompt-actions">
                <button className="grocery-prompt-dismiss" onClick={handleRegularsDismiss}>Not this time</button>
                <button className="grocery-prompt-submit" onClick={handleRegularsSubmit}>
                  Add to list {regularsChecked.size > 0 ? `(${regularsChecked.size})` : ''}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {!pantry_checked && (
        <div className="grocery-prompt-card">
          {!pantryExpanded ? (
            <button className="grocery-prompt-trigger" onClick={handlePantryExpand}>
              <span className="grocery-prompt-icon">{'\u{1F3E0}'}</span>
              <span>Running low on anything?</span>
              <span className="grocery-prompt-arrow">{'\u203A'}</span>
            </button>
          ) : (
            <div className="grocery-prompt-body">
              <div className="grocery-prompt-title">Pantry check</div>
              <div className="grocery-prompt-desc">Check anything you need to restock.</div>
              {pantryData && pantryData.length > 0 ? (
                <div className="grocery-prompt-checklist">
                  {pantryData.map(p => (
                    <div
                      key={p.id}
                      className="grocery-prompt-check-item"
                      onClick={() => {
                        setPantryChecked(prev => {
                          const next = new Set(prev)
                          next.has(p.name) ? next.delete(p.name) : next.add(p.name)
                          return next
                        })
                      }}
                    >
                      <div className={`grocery-prompt-check ${pantryChecked.has(p.name) ? 'active' : ''}`}>
                        {pantryChecked.has(p.name) && '\u2713'}
                      </div>
                      <span>{p.name}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="grocery-prompt-empty">No pantry items yet. Add them in My Kitchen.</div>
              )}
              <div className="grocery-prompt-actions">
                <button className="grocery-prompt-dismiss" onClick={handlePantryDismiss}>Skip</button>
                <button className="grocery-prompt-submit" onClick={handlePantrySubmit}>
                  Add to list {pantryChecked.size > 0 ? `(${pantryChecked.size})` : ''}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  )

  const isItemHidden = (nameLower) => {
    return hideDone && (checkedSet.has(nameLower) || skippedSet.has(nameLower))
  }

  const listContent = (
    <>
      {hasItems && doneCount > 0 && (
        <button className="hide-checked-toggle" onClick={() => setHideDone(h => !h)}>
          {hideDone ? `Show checked & skipped` : `Hide checked & skipped`} ({doneCount})
        </button>
      )}
      {!hasItems ? (
        <div className="empty-state">
          <div className="icon">{'\u{1F6D2}'}</div>
          <p>No items yet. Tap the cart icon on a meal to add its ingredients.</p>
        </div>
      ) : (
        sortedGroups.map(group => {
          const items = items_by_group[group]
          const { remaining: groupLeft } = groupCounts[group]
          const expanded = isGroupExpanded(group)
          const allDone = isGroupAllDone(group)

          return (
            <div key={group} className="grocery-group">
              <button
                className={`grocery-group-header ${allDone ? 'all-done' : ''}`}
                onClick={() => handleGroupToggle(group)}
              >
                <span className="grocery-group-arrow">{expanded ? '\u25B4' : '\u25BE'}</span>
                <span className="grocery-group-title">{group}</span>
                {groupLeft > 0 ? (
                  <span className="group-left-count">{groupLeft} left</span>
                ) : (
                  <span className="group-left-count done">{'\u2713'} done</span>
                )}
              </button>
              {expanded && items.filter(item => !isItemHidden(item.name.toLowerCase())).map(item => {
                const nameLower = item.name.toLowerCase()
                const isChecked = checkedSet.has(nameLower)
                const isOrdered = orderedSet.has(nameLower)
                const isSkipped = skippedSet.has(nameLower)
                const stateClass = isChecked ? 'checked' : isOrdered ? 'ordered' : isSkipped ? 'skipped' : ''
                return (
                  <div
                    key={item.name}
                    className={`grocery-item ${stateClass}`}
                  >
                    {isOrdered ? (
                      <>
                        <span className="check ordered">{'\u2191'}</span>
                        <span className="item-name ordered-text">
                          {item.name}
                          {item.meal_count > 1 && <span className="multi-badge">x{item.meal_count}</span>}
                        </span>
                        {item.for_meals && item.for_meals.length > 0 && (
                          <span className="item-meals">{item.for_meals.join(', ')} {'\u00B7'} ordered</span>
                        )}
                      </>
                    ) : (
                      <>
                        <span className={`item-name ${isChecked ? 'done-text' : isSkipped ? 'skipped-text' : ''}`}>
                          {item.name}
                          {item.meal_count > 1 && <span className="multi-badge">x{item.meal_count}</span>}
                        </span>
                        {item.for_meals && item.for_meals.length > 0 && (
                          <span className="item-meals">{item.for_meals.join(', ')}</span>
                        )}
                        <div className="grocery-item-toggle">
                          <button
                            className={`toggle-seg bought ${isChecked ? 'active' : ''}`}
                            onClick={() => handleToggle(item.name)}
                          >Bought</button>
                          <button
                            className={`toggle-seg skip ${isSkipped ? 'active' : ''}`}
                            onClick={() => handleSkip(item.name)}
                          >Skip</button>
                        </div>
                        <button
                          className="recat-btn"
                          title="Move to different aisle"
                          onClick={(e) => { e.stopPropagation(); setRecatItem(item.name) }}
                        >{'\u2630'}</button>
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          )
        })
      )}
    </>
  )

  const addBar = (
    <div className={`add-bar ${sidebar ? '' : 'add-bar-mobile'}`}>
      <div className="add-form">
        <AutocompleteInput
          value={addText}
          onChange={(val) => {
            setAddText(val)
            setAddDupe(val.trim() && onListSet.has(val.trim().toLowerCase()))
          }}
          onSubmit={handleAddSubmit}
          candidates={itemPool}
          exclude={onListSet}
          placeholder="Anything else while you're there?"
          inputClassName={`add-input${addDupe ? ' prefs-dupe' : ''}`}
        />
        <button className="btn primary" onClick={() => addText.trim() && handleAddSubmit(addText)} disabled={addDupe}>+</button>
      </div>
      {addDupe && <div className="prefs-dupe-msg" style={{ marginTop: 4 }}>Already on your list</div>}
    </div>
  )

  const formatTripSubtitle = () => {
    if (!start_date) return ''
    const s = new Date(start_date + 'T00:00:00')
    const month = s.toLocaleDateString('en-US', { month: 'short' })
    const day = s.getDate()
    const itemText = `${remainingCount} item${remainingCount !== 1 ? 's' : ''} left`
    return `${month} ${day} trip \u00B7 ${itemText}`
  }

  const sidebarTitleBlock = (
    <div className="sidebar-title">
      <span>Grocery List</span>
      {remainingCount > 0 && (
        <span className="count-badge">
          {remainingCount} item{remainingCount !== 1 ? 's' : ''} left
        </span>
      )}
    </div>
  )

  const mobileTitleBlock = (
    <div className="page-header">
      <h2 className="screen-heading">Grocery List</h2>
      <div className="screen-sub">{formatTripSubtitle()}</div>
    </div>
  )

  return (
    <>
      {sidebar ? (
        <>
          <div className="sidebar-card">
            {sidebarTitleBlock}
            {promptCards}
            {listContent}
          </div>
          {addBar}
        </>
      ) : (
        <>
          {mobileTitleBlock}
          {addBar}
          {promptCards}
          {listContent}
        </>
      )}

      {recatItem && (
        <Sheet onClose={() => setRecatItem(null)}>
          <div className="sheet-title">Move "{recatItem}"</div>
          <div className="sheet-sub">Pick a shopping group</div>
          <div className="recat-options">
            {GROUP_ORDER.map(g => (
              <button
                key={g}
                className="recat-option"
                onClick={() => handleRecategorize(g)}
              >{g}</button>
            ))}
          </div>
        </Sheet>
      )}
      <FeedbackFab page="grocery" />
    </>
  )
}
