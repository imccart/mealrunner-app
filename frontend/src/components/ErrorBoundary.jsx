import { Component } from 'react'
import FeedbackFab from './FeedbackFab'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  render() {
    if (this.state.error) {
      return <ErrorScreen onRefresh={() => window.location.reload()} />
    }
    return this.props.children
  }
}

export function ErrorScreen({ onRefresh }) {
  return (
    <div className="error-boundary">
      <div className="error-boundary-scene">
        <div className="dropped-bag">
          <div className="bag-body">
            <div className="bag-handle-left"></div>
            <div className="bag-handle-right"></div>
            <div className="bag-front"></div>
          </div>
          <div className="spill spill-1">{'\u{1F966}'}</div>
          <div className="spill spill-2">{'\u{1F34E}'}</div>
          <div className="spill spill-3">{'\u{1F956}'}</div>
          <div className="spill spill-4">{'\u{1F95A}'}</div>
          <div className="spill spill-5">{'\u{1F955}'}</div>
        </div>
      </div>
      <h2 className="error-boundary-title">We dropped something</h2>
      <p className="error-boundary-sub">
        Sorry about the mess. Try refreshing, or let us know what happened.
      </p>
      <div className="error-boundary-actions">
        <button className="error-boundary-btn refresh" onClick={onRefresh}>
          Try again
        </button>
      </div>
      <FeedbackFab page="error" />
    </div>
  )
}

export function CrashTest() {
  throw new Error('Test crash — everything is fine!')
}
