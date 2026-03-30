import { useState, useEffect } from 'react'
import { api } from '../api/client'

export default function LearningSuggestions({ onChange }) {
  const [suggestions, setSuggestions] = useState(null)

  useEffect(() => {
    api.getLearningSuggestions()
      .then(setSuggestions)
      .catch(() => setSuggestions({ add: [] }))
  }, [])

  if (!suggestions) return null

  const { add } = suggestions
  if (add.length === 0) return null

  const handleAddYes = async (name) => {
    await api.addRegular(name)
    setSuggestions(prev => ({
      ...prev,
      add: prev.add.filter(s => s.name !== name),
    }))
    onChange?.()
  }

  const handleDismiss = async (name) => {
    await api.dismissLearning(name)
    setSuggestions(prev => ({
      add: prev.add.filter(s => s.name !== name),
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
    </div>
  )
}
