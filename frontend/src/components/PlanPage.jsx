import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import Sheet from './Sheet'
import MealPickerSheet from './MealPickerSheet'
import SwapPrompt from './SwapPrompt'
import SidePickerSheet from './SidePickerSheet'
import MealIngredientsSheet from './MealIngredientsSheet'
import FeedbackFab from './FeedbackFab'

function formatDateRange(start, end) {
  if (!start || !end) return ''
  const s = new Date(start + 'T00:00:00')
  const e = new Date(end + 'T00:00:00')
  const sMonth = s.toLocaleDateString('en-US', { month: 'short' })
  const eMonth = e.toLocaleDateString('en-US', { month: 'short' })
  if (sMonth === eMonth) {
    return { text: `${sMonth} ${s.getDate()}`, endText: `${e.getDate()}` }
  }
  return { text: `${sMonth} ${s.getDate()}`, endText: `${eMonth} ${e.getDate()}` }
}

function isToday(dateStr) {
  return dateStr === new Date().toISOString().split('T')[0]
}

export default function PlanPage({ showHeader = true, onLoad, onNavigate }) {
  const [data, setData] = useState(null)
  const [actionDate, setActionDate] = useState(null) // date for action bottom sheet
  const [pickerDate, setPickerDate] = useState(null) // date for meal picker
  const [pickerMode, setPickerMode] = useState(null) // 'add' or 'replace'
  const [swapPrompt, setSwapPrompt] = useState(null)
  const [loading, setLoading] = useState(true)
  const [dragFrom, setDragFrom] = useState(null)
  const [pastDays, setPastDays] = useState(null)
  const [showPast, setShowPast] = useState(false)
  const [sidePickerDate, setSidePickerDate] = useState(null)
  const [ingredientsMeal, setIngredientsMeal] = useState(null)
  const [erasing, setErasing] = useState(false)

  // Touch drag refs
  const touchTimer = useRef(null)
  const touchDragFrom = useRef(null)
  const didDrag = useRef(false)
  const rowsRef = useRef(null)

  const [loadError, setLoadError] = useState(false)

  const load = async () => {
    try {
      const result = await api.getMeals()
      setData(result)
    } catch {
      setLoadError(true)
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  useEffect(() => {
    if (data && onLoad) onLoad(data)
  }, [data, onLoad])

  // Touch drag handlers
  const handleTouchStart = useCallback((e, date) => {
    didDrag.current = false
    touchTimer.current = setTimeout(() => {
      touchDragFrom.current = date
      didDrag.current = true
      setDragFrom(date)
      if (navigator.vibrate) navigator.vibrate(50)
    }, 400)
  }, [])

  const handleTouchMove = useCallback((e) => {
    if (!touchDragFrom.current) {
      // If finger moves before long-press fires, cancel it
      clearTimeout(touchTimer.current)
      return
    }
    e.preventDefault()
    const touch = e.touches[0]
    const el = document.elementFromPoint(touch.clientX, touch.clientY)
    const row = el?.closest('.meal-row, .add-meal-row')
    if (rowsRef.current) {
      rowsRef.current.querySelectorAll('.touch-drop-hover').forEach(
        n => n.classList.remove('touch-drop-hover')
      )
    }
    if (row && row.dataset.date !== touchDragFrom.current) {
      row.classList.add('touch-drop-hover')
    }
  }, [])

  const handleTouchEnd = useCallback(async (e) => {
    clearTimeout(touchTimer.current)
    if (!touchDragFrom.current) return

    const touch = e.changedTouches[0]
    const el = document.elementFromPoint(touch.clientX, touch.clientY)
    const row = el?.closest('.meal-row, .add-meal-row')
    const targetDate = row?.dataset.date

    if (targetDate && targetDate !== touchDragFrom.current) {
      try {
        const result = await api.swapDays(touchDragFrom.current, targetDate)
        setData(result)
      } catch { /* silent — rows snap back */ }
    }

    if (rowsRef.current) {
      rowsRef.current.querySelectorAll('.touch-drop-hover').forEach(
        n => n.classList.remove('touch-drop-hover')
      )
    }
    touchDragFrom.current = null
    setDragFrom(null)
    e.preventDefault()
  }, [])

  if (loading) return <><div className="loading">Setting the table...</div><FeedbackFab page="plan" /></>
  if (loadError) return <><div className="loading">Something went wrong loading meals. Try refreshing.</div><FeedbackFab page="plan" /></>
  if (!data) return null

  const { days, start_date, end_date } = data
  const dateRange = formatDateRange(start_date, end_date)
  const hasMeals = days.some(d => d.meal)

  // ── Tap handlers ──

  const handleMealTap = (date) => {
    // Suppress tap if we just finished a drag
    if (didDrag.current) {
      didDrag.current = false
      return
    }
    setActionDate(actionDate === date ? null : date)
  }

  const handleEmptyTap = (date) => {
    setPickerDate(date)
    setPickerMode('add')
  }

  const handleReplace = (date) => {
    setActionDate(null)
    setPickerDate(date)
    setPickerMode('replace')
  }

  const handleToggleGrocery = async (date) => {
    try {
      const result = await api.toggleGrocery(date)
      setData(result)
    } catch { /* silent — checkbox stays in current state */ }
  }

  const handleSetMeal = async (date, recipeId, sides) => {
    try {
      const result = await api.setMeal(date, recipeId, sides)
      setData(result)
      setPickerDate(null)
      setPickerMode(null)
    } catch { await load() }
  }

  const handleFreeform = async (date, name) => {
    try {
      const result = await api.setFreeform(date, name)
      setData(result)
      setPickerDate(null)
      setPickerMode(null)
      setActionDate(null)
    } catch { await load() }
  }

  const handleOpenSidePicker = (date) => {
    setActionDate(null)
    setSidePickerDate(date)
  }

  const handleSetSide = async (date, sides) => {
    try {
      const result = await api.setSide(date, sides)
      setData(result)
      setSidePickerDate(null)
    } catch { await load() }
  }


  const handleSwapConfirm = async (choices) => {
    try {
      const result = await api.swapMealSmart(swapPrompt.date, choices)
      setData(result)
      setSwapPrompt(null)
    } catch { await load() }
  }

  const handleStartNewPlan = async () => {
    if (!window.confirm('This clears all your meals and your grocery list. Are you sure?')) return
    setErasing(true)
    setTimeout(async () => {
      try {
        const result = await api.freshStart()
        setData(result)
      } catch { await load() }
      setErasing(false)
    }, 700)
  }

  const handleViewPast = async () => {
    if (showPast) {
      setShowPast(false)
      return
    }
    try {
      const result = await api.getPastMeals()
      setPastDays(result.days)
      setShowPast(true)
    } catch { /* silent — toggle stays off */ }
  }

  const actionDay = actionDate ? days.find(d => d.date === actionDate) : null
  const actionMeal = actionDay?.meal
  const actionIsFreeform = actionMeal && !actionMeal.recipe_id
  const actionHasSide = actionMeal && actionMeal.sides?.length > 0 && !actionIsFreeform
  const actionDayName = actionDate
    ? new Date(actionDate + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'long' })
    : ''

  // Get day name for picker
  const pickerDay = pickerDate ? days.find(d => d.date === pickerDate) : null
  const pickerDayName = pickerDay
    ? new Date(pickerDate + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'long' })
    : ''

  // Only show "Tap to add a meal" on the first empty day
  let firstEmptyShown = false

  return (
    <>
      {showHeader && (
        <>
          <div className="page-header">
            <div className="date-range-big">
              {dateRange.text} <em>&ndash;</em> {dateRange.endText}
            </div>
            <div className="date-subtitle">Your next 10 days</div>
          </div>
        </>
      )}

      {/* Past meals (read-only) */}
      <div className="past-toggle" onClick={handleViewPast}>
        {showPast ? 'Hide past meals' : 'View past meals'}
      </div>
      {showPast && pastDays && (
        <div className="meal-rows past-meals">
          {pastDays.map(({ date, day_short, meal }) => (
            <div key={date} className="meal-row past">
              <div className="meal-day">{day_short}</div>
              <div className="meal-info">
                {meal ? (
                  <>
                    <div className="meal-name">{meal.recipe_name}</div>
                    {meal.sides?.length > 0 && <div className="meal-side-text">{meal.sides.map(s => s.name).join(', ')}</div>}
                  </>
                ) : (
                  <div className="meal-name freeform">No meal</div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className={`meal-rows${erasing ? ' erasing' : ''}`} ref={rowsRef}>
        {days.map(({ date, day_short, meal }, idx) => {
          const today = isToday(date)
          const hasMeal = !!meal
          const isFreeform = hasMeal && !meal.recipe_id
          const onList = hasMeal && meal.on_grocery && !isFreeform
          const isDragging = dragFrom === date

          if (!hasMeal) {
            const showHint = !firstEmptyShown
            firstEmptyShown = true
            return (
              <div
                key={date}
                data-date={date}
                className={`add-meal-row ${today ? 'today' : ''}`}
                style={{ '--row-index': idx }}
                onClick={() => handleEmptyTap(date)}
              >
                <div className="meal-day">{day_short}</div>
                <div className="add-label">
                  {showHint ? 'Tap to add a meal' : '+'}
                </div>
              </div>
            )
          }

          return (
            <div
              key={date}
              data-date={date}
              style={{ '--row-index': idx }}
              className={`meal-row ${today ? 'today' : ''} ${onList ? 'on-list' : ''} ${isDragging ? 'dragging' : ''}`}
              onClick={() => handleMealTap(date)}
              onTouchStart={(e) => handleTouchStart(e, date)}
              onTouchMove={handleTouchMove}
              onTouchEnd={handleTouchEnd}
            >
              <div className="meal-day">{day_short}</div>
              <div className="meal-info">
                <div className={`meal-name ${isFreeform ? 'freeform' : ''}`}>{meal.recipe_name}</div>
                {meal.sides?.length > 0 && <div className="meal-side-text">{meal.sides.map(s => s.name).join(', ')}</div>}
              </div>
              <div className="meal-actions" onClick={(e) => e.stopPropagation()}>
                {!isFreeform && (
                  <button
                    className={`meal-btn ${meal.on_grocery ? 'on-list' : ''}`}
                    onClick={() => handleToggleGrocery(date)}
                    title={meal.on_grocery ? 'On list' : 'Add to list'}
                  >{meal.on_grocery ? '\u2713' : '\u{1F6D2}'}</button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Action bottom sheet for tapped meal */}
      {actionDate && actionMeal && (
        <Sheet onClose={() => setActionDate(null)}>
            <div className="sheet-title">{actionDayName}</div>
            <div className="sheet-sub">{actionMeal.recipe_name}{actionMeal.sides?.length > 0 ? ` + ${actionMeal.sides.map(s => s.name).join(', ')}` : ''}</div>
            <div className="sheet-options">
              <button className="sheet-option" onClick={() => handleReplace(actionDate)}>
                <div className="sheet-opt-icon">{'\u{1F504}'}</div>
                <div>
                  <div className="sheet-opt-title">Different meal</div>
                  <div className="sheet-opt-desc">Pick something else</div>
                </div>
              </button>
              {!actionIsFreeform && (
                <button className="sheet-option" onClick={() => handleOpenSidePicker(actionDate)}>
                  <div className="sheet-opt-icon">{'\u{1F951}'}</div>
                  <div>
                    <div className="sheet-opt-title">{actionHasSide ? 'Change sides' : 'Add sides'}</div>
                    <div className="sheet-opt-desc">{actionHasSide ? 'Keep the meal, change side dishes' : 'Pick side dishes for this meal'}</div>
                  </div>
                </button>
              )}
              <button className="sheet-option" onClick={() => { setIngredientsMeal(actionMeal); setActionDate(null) }}>
                <div className="sheet-opt-icon">{'\u{1F4CB}'}</div>
                <div>
                  <div className="sheet-opt-title">Ingredients</div>
                  <div className="sheet-opt-desc">View or edit what goes into this meal</div>
                </div>
              </button>
              <button className="sheet-option" onClick={() => handleFreeform(actionDate, 'Nothing Planned')}>
                <div className="sheet-opt-icon">{'\u{1F44B}'}</div>
                <div>
                  <div className="sheet-opt-title">Nothing needed</div>
                  <div className="sheet-opt-desc">Eating out, leftovers, winging it</div>
                </div>
              </button>
            </div>
        </Sheet>
      )}



      {/* Plan footer */}
      <div className="plan-footer">
        <button className="fresh-start-btn" onClick={handleStartNewPlan}>
          {'\u{1F9F9}'} Fresh Start
        </button>
      </div>

      <FeedbackFab page="plan" />

      {/* Meal picker sheet */}
      {pickerDate && (
        <MealPickerSheet
          date={pickerDate}
          dayName={pickerDayName}
          onSelect={(recipeId, sides) => handleSetMeal(pickerDate, recipeId, sides)}
          onFreeform={(name) => handleFreeform(pickerDate, name)}
          onClose={() => { setPickerDate(null); setPickerMode(null) }}
        />
      )}


      {/* Side picker sheet */}
      {sidePickerDate && (
        <SidePickerSheet
          date={sidePickerDate}
          mealName={days.find(d => d.date === sidePickerDate)?.meal?.recipe_name || ''}
          onSelect={(sides) => handleSetSide(sidePickerDate, sides)}
          onClose={() => setSidePickerDate(null)}
        />
      )}

      {/* Meal ingredients sheet */}
      {ingredientsMeal && (
        <MealIngredientsSheet
          meal={ingredientsMeal}
          onClose={() => setIngredientsMeal(null)}
        />
      )}

      {/* Swap prompt */}
      {swapPrompt && (
        <SwapPrompt
          prompt={swapPrompt}
          onConfirm={handleSwapConfirm}
          onClose={() => setSwapPrompt(null)}
        />
      )}
    </>
  )
}
