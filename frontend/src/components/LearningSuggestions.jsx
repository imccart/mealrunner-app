import { useState, useEffect } from 'react'
import { api } from '../api/client'

export default function LearningSuggestions({ onChange }) {
  const [suggestions, setSuggestions] = useState(null)

  useEffect(() => {
    api.getLearningSuggestions()
      .then(setSuggestions)
      .catch(() => setSuggestions({ add: [], remove: [] }))
  }, [])

  if (!suggestions) return null

  const { add, remove } = suggestions
  if (add.length === 0 && remove.length === 0) return null

  const handleAddYes = async (name) => {
    await api.addRegular(name)
    setSuggestions(prev => ({
      ...prev,
      add: prev.add.filter(s => s.name !== name),
    }))
    onChange?.()
  }

  const handleRemoveYes = async (id, name) => {
    await api.toggleRegular(id)
    setSuggestions(prev => ({
      ...prev,
      remove: prev.remove.filter(s => s.id !== id),
    }))
    onChange?.()
  }

  const handleDismiss = async (name) => {
    await api.dismissLearning(name)
    setSuggestions(prev => ({
      add: prev.add.filter(s => s.name !== name),
      remove: prev.remove.filter(s => s.name.toLowerCase() !== name.toLowerCase()),
    }))
  }

  return (
    <div style={{ marginBottom: '16px' }}>
      {add.map(s => (
        <div key={s.name} className="learning-card">
          <div className="learning-card-text">
            <em>{s.name}</em> shows up on almost every list. Add to regulars?
          </div>
          <div className="learning-card-actions">
            <button className="learning-btn yes" onClick={() => handleAddYes(s.name)}>Yes</button>
            <button className="learning-btn no" onClick={() => handleDismiss(s.name)}>Not now</button>
          </div>
        </div>
      ))}
      {remove.map(s => (
        <div key={s.id} className="learning-card">
          <div className="learning-card-text">
            You've been skipping <em>{s.name}</em>. Remove from regulars?
          </div>
          <div className="learning-card-actions">
            <button className="learning-btn yes" onClick={() => handleRemoveYes(s.id, s.name)}>Yes</button>
            <button className="learning-btn no" onClick={() => handleDismiss(s.name)}>Not now</button>
          </div>
        </div>
      ))}
    </div>
  )
}
