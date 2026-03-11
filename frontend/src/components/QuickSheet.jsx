export default function QuickSheet({ onClose }) {
  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="sheet-title">What do you need?</div>
        <div className="sheet-sub">Add to your plan or start a focused list</div>
        <div className="sheet-options">
          <button className="sheet-option" onClick={onClose}>
            <div className="sheet-opt-icon">{'\u{1F5D3}'}</div>
            <div className="sheet-opt-info">
              <div className="sheet-opt-title">Add a Meal</div>
              <div className="sheet-opt-desc">Add one meal to your current plan</div>
            </div>
          </button>
          <button className="sheet-option" onClick={onClose}>
            <div className="sheet-opt-icon">{'\u{1F37D}'}</div>
            <div className="sheet-opt-info">
              <div className="sheet-opt-title">Single Meal List</div>
              <div className="sheet-opt-desc">One meal, quick focused list</div>
            </div>
          </button>
          <button className="sheet-option" onClick={onClose}>
            <div className="sheet-opt-icon">{'\u{1F3C3}'}</div>
            <div className="sheet-opt-info">
              <div className="sheet-opt-title">Grocery Run</div>
              <div className="sheet-opt-desc">Quick trip, start from your regulars</div>
            </div>
          </button>
        </div>
      </div>
    </div>
  )
}
