import { useState, useEffect } from 'react'
import { api } from '../api/client'

export default function LearningSuggestions({ onChange }) {
  const [suggestions, setSuggestions] = useState(null)

  useEffect(() => {
    api.getLearningSuggestions()
      .then(setSuggestions)
      .catch(() => setSuggestions({ add: [], remove_regulars: [], restock_staples: [] }))
  }, [])

  if (!suggestions) return null

  const { add = [], remove_regulars = [], restock_staples = [] } = suggestions
  if (add.length === 0 && remove_regulars.length === 0 && restock_staples.length === 0) return null

  const handleAddYes = async (name) => {
    await api.addRegular(name)
    setSuggestions(prev => ({ ...prev, add: prev.add.filter(s => s.name !== name) }))
    onChange?.()
  }

  const handleRemoveYes = async (id, name) => {
    await api.toggleRegular(id)
    setSuggestions(prev => ({ ...prev, remove_regulars: prev.remove_regulars.filter(s => s.id !== id) }))
    onChange?.()
  }

  const handleRestockYes = async (name) => {
    await api.addGroceryItem(name)
    setSuggestions(prev => ({ ...prev, restock_staples: prev.restock_staples.filter(s => s.name !== name) }))
    onChange?.()
  }

  const handleDismiss = async (name) => {
    await api.dismissLearning(name)
    setSuggestions(prev => ({
      add: prev.add.filter(s => s.name !== name),
      remove_regulars: prev.remove_regulars.filter(s => s.name.toLowerCase() !== name.toLowerCase()),
      restock_staples: prev.restock_staples.filter(s => s.name.toLowerCase() !== name.toLowerCase()),
    }))
  }

  return (
    <div style={{ marginBottom: '16px' }}>
      {add.map(s => (
        <div key={`add-${s.name}`} className="learning-card">
          <div className="learning-card-text">
            <em>{s.name}</em> shows up on almost every list. Add to regulars?
          </div>
          <div className="learning-card-actions">
            <button className="learning-btn yes" onClick={() => handleAddYes(s.name)}>Yes</button>
            <button className="learning-btn no" onClick={() => handleDismiss(s.name)}>Not now</button>
          </div>
        </div>
      ))}
      {remove_regulars.map(s => (
        <div key={`rm-${s.id}`} className="learning-card">
          <div className="learning-card-text">
            You haven't bought <em>{s.name}</em> in a while. Remove from regulars?
          </div>
          <div className="learning-card-actions">
            <button className="learning-btn yes" onClick={() => handleRemoveYes(s.id, s.name)}>Yes</button>
            <button className="learning-btn no" onClick={() => handleDismiss(s.name)}>Not now</button>
          </div>
        </div>
      ))}
      {restock_staples.map(s => (
        <div key={`restock-${s.id}`} className="learning-card">
          <div className="learning-card-text">
            Running low on <em>{s.name}</em>? Add to your grocery list?
          </div>
          <div className="learning-card-actions">
            <button className="learning-btn yes" onClick={() => handleRestockYes(s.name)}>Yes</button>
            <button className="learning-btn no" onClick={() => handleDismiss(s.name)}>Not now</button>
          </div>
        </div>
      ))}
    </div>
  )
}
