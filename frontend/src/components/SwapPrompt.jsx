import { useState } from 'react'
import Sheet from './Sheet'

export default function SwapPrompt({ prompt, onConfirm, onClose }) {
  const { date, old_meal, new_meal, removable, old_was_on_list } = prompt
  // removable is [{id, name}]. Track selection by id so multiple meal-source
  // rows that happen to share a canonical name (rare post-dedup but possible)
  // can be toggled independently.
  const [removeIds, setRemoveIds] = useState(new Set(removable.map(r => r.id)))
  const [step, setStep] = useState(removable.length > 0 ? 'remove' : 'add')

  const toggleRemove = (id) => {
    const next = new Set(removeIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setRemoveIds(next)
  }

  const handleRemoveDone = () => {
    setStep('add')
  }

  const handleAddChoice = (addToList) => {
    onConfirm({
      action: 'confirm',
      remove_items: [...removeIds],
      add_to_list: addToList,
    })
  }

  return (
    <Sheet onClose={onClose}>
      {step === 'remove' && (
        <>
          <div className="sheet-title">Remove from list?</div>
          <div className="sheet-sub">
            {old_meal} was replaced. These ingredients aren't needed by other meals:
          </div>
          <div className="carry-items">
            {removable.map(item => (
              <div key={item.id} className="carry-item" onClick={() => toggleRemove(item.id)}>
                <div className={`carry-check ${removeIds.has(item.id) ? 'active' : ''}`}>
                  {removeIds.has(item.id) ? '\u2713' : ''}
                </div>
                <span>{item.name}</span>
              </div>
            ))}
          </div>
          <div className="sheet-btn-row">
            <button className="sheet-btn-secondary" onClick={() => {
              setRemoveIds(new Set())
              handleRemoveDone()
            }}>Keep all</button>
            <button className="sheet-btn-primary" onClick={handleRemoveDone}>
              Remove selected {'\u2192'}
            </button>
          </div>
        </>
      )}

      {step === 'add' && (
        <>
          <div className="sheet-title">Add {new_meal} to your list?</div>
          <div className="sheet-sub">
            Add its ingredients to your grocery list, or mark it as covered.
          </div>
          <div className="sheet-btn-row">
            <button className="sheet-btn-secondary" onClick={() => handleAddChoice(false)}>
              Already covered
            </button>
            <button className="sheet-btn-primary" onClick={() => handleAddChoice(true)}>
              Add to list {'\u2192'}
            </button>
          </div>
        </>
      )}
    </Sheet>
  )
}
