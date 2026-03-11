import { StrictMode, Component } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 20, color: 'red', fontFamily: 'monospace', fontSize: 14 }}>
          <h2>Render Error</h2>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{String(this.state.error.message)}</pre>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 10 }}>{String(this.state.error.stack)}</pre>
        </div>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
