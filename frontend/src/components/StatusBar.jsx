export default function StatusBar({ status }) {
  if (!status) return null

  const line = buildStatusLine(status)
  if (!line) return null

  return <div className="status-line">{line}</div>
}

function buildStatusLine(s) {
  const parts = []

  // Meals planned
  if (s.total_meals === 0) {
    parts.push('No meals planned')
  } else {
    parts.push(`${s.total_meals} meal${s.total_meals !== 1 ? 's' : ''} planned`)
  }

  // Grocery state
  if (s.total_meals > 0 && s.meals_on_grocery < s.total_meals) {
    const remaining = s.total_meals - s.meals_on_grocery
    parts.push(`${remaining} not on list`)
  } else if (s.total_meals > 0 && !s.grocery_built) {
    parts.push('list not started')
  } else if (s.grocery_built && !s.order_placed) {
    parts.push('ready to order')
  } else if (s.order_placed && s.reconcile_count === 0) {
    parts.push('order placed')
  } else if (s.reconcile_count > 0) {
    parts.push(`receipt reconciled`)
  }

  return parts.join(' \u00b7 ')
}
