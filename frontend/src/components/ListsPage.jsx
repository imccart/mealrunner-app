import { useState, useEffect } from 'react'
import { api } from '../api/client'

function formatTripDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00')
  const month = d.toLocaleDateString('en-US', { month: 'short' })
  return `${month} ${d.getDate()}`
}

function tripTypeLabel(tripType) {
  if (tripType === 'plan') return 'Weekly Shop'
  if (tripType === 'quick') return 'Quick Run'
  if (tripType === 'single') return 'Single Meal'
  return 'Grocery Trip'
}

function badgeInfo(trip) {
  if (trip.active) {
    return { label: 'In progress', className: 'active' }
  }
  if (trip.total_items > 0 && trip.checked_items >= trip.total_items) {
    return { label: 'Complete', className: 'complete' }
  }
  if (trip.checked_items > 0) {
    return { label: 'Partial', className: 'partial' }
  }
  return { label: 'Complete', className: 'complete' }
}

function metaText(trip) {
  if (trip.total_items === 0) return 'No items'
  if (trip.checked_items >= trip.total_items) {
    return `${trip.total_items} item${trip.total_items !== 1 ? 's' : ''} \u00B7 all checked`
  }
  return `${trip.checked_items} of ${trip.total_items} items checked`
}

export default function ListsPage() {
  const [trips, setTrips] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getGroceryTrips().then(data => {
      setTrips(data.trips || [])
      setLoading(false)
    })
  }, [])

  if (loading) return <div className="loading">Checking the pantry...</div>

  const activeTrips = trips.filter(t => t.active)
  const pastTrips = trips.filter(t => !t.active)

  return (
    <>
      <div className="page-header">
        <h2 className="screen-heading">Your Lists</h2>
        <div className="screen-sub">All grocery trips</div>
      </div>

      {activeTrips.length > 0 && (
        <>
          <div className="section-label">Active</div>
          {activeTrips.map(trip => {
            const badge = badgeInfo(trip)
            return (
              <div key={trip.id} className="trip-card">
                <div className="trip-header">
                  <div className="trip-title">
                    {formatTripDate(trip.start_date)} {'\u2014'} {tripTypeLabel(trip.trip_type)}
                  </div>
                  <div className={`trip-badge ${badge.className}`}>{badge.label}</div>
                </div>
                <div className="trip-meta">{metaText(trip)}</div>
                {trip.preview && (
                  <div className="trip-items-preview">{trip.preview}</div>
                )}
              </div>
            )
          })}
        </>
      )}

      {pastTrips.length > 0 && (
        <>
          <div className="section-label" style={{ marginTop: activeTrips.length > 0 ? 16 : 0 }}>
            Past Trips
          </div>
          {pastTrips.map(trip => {
            const badge = badgeInfo(trip)
            return (
              <div key={trip.id} className="trip-card">
                <div className="trip-header">
                  <div className="trip-title">
                    {formatTripDate(trip.start_date)} {'\u2014'} {tripTypeLabel(trip.trip_type)}
                  </div>
                  <div className={`trip-badge ${badge.className}`}>{badge.label}</div>
                </div>
                <div className="trip-meta">{metaText(trip)}</div>
                {trip.preview && (
                  <div className="trip-items-preview">{trip.preview}</div>
                )}
              </div>
            )
          })}
        </>
      )}

      {trips.length === 0 && (
        <div className="empty-state">
          <div className="icon">{'\u{1F4CB}'}</div>
          <p>No grocery trips yet. Build a list from the Grocery page to get started.</p>
        </div>
      )}
    </>
  )
}
