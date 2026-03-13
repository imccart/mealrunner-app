import { useState } from 'react'
import Sheet from './Sheet'

export default function SwapPrompt({ prompt, onConfirm, onClose }) {
  const { date, old_meal, new_meal, removable, old_was_on_list } = prompt
  const [removeItems, setRemoveItems] = useState(new Set(removable))
  const [step, setStep] = useState(removable.length > 0 ? 'remove' : 'add')

  const toggleRemove = (name) => {
    const next = new Set(removeItems)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    setRemoveItems(next)
  }

  const handleRemoveDone = () => {
    setStep('add')
  }

  const handleAddChoice = (addToList) => {
    onConfirm({
      action: 'confirm',
      remove_items: [...removeItems],
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
            {removable.map(name => (
              <div key={name} className="carry-item" onClick={() => toggleRemove(name)}>
                <div className={`carry-check ${removeItems.has(name) ? 'active' : ''}`}>
                  {removeItems.has(name) ? '\u2713' : ''}
                </div>
                <span>{name}</span>
              </div>
            ))}
          </div>
          <div className="sheet-btn-row">
            <button className="sheet-btn-secondary" onClick={() => {
              setRemoveItems(new Set())
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
