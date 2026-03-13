import { Component } from 'react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null, reported: false, reporting: false }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  handleReport = async () => {
    this.setState({ reporting: true })
    try {
      const msg = `[Error Boundary] ${this.state.error.message}\n\n${this.state.error.stack || ''}`
      await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, page: 'error-boundary' }),
      })
      this.setState({ reported: true })
    } catch {
      window.location.reload()
    }
    this.setState({ reporting: false })
  }
  render() {
    if (this.state.error) {
      return <ErrorScreen
        onRefresh={() => window.location.reload()}
        onReport={this.handleReport}
        reporting={this.state.reporting}
        reported={this.state.reported}
      />
    }
    return this.props.children
  }
}

export function ErrorScreen({ onRefresh, onReport, reporting, reported }) {
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
        {onReport && !reported ? (
          <button
            className="error-boundary-btn report"
            onClick={onReport}
            disabled={reporting}
          >
            {reporting ? 'Reporting...' : 'Talk to the manager'}
          </button>
        ) : reported ? (
          <div className="error-boundary-reported">Reported. We're on it.</div>
        ) : null}
      </div>
    </div>
  )
}

export function CrashTest() {
  throw new Error('Test crash — everything is fine!')
}
