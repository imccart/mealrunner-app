import { useState, useEffect } from 'react'
import { api } from '../api/client'

export default function ReceiptPage() {
  const [receipt, setReceipt] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadResult, setUploadResult] = useState(null)
  const [pasteText, setPasteText] = useState('')
  const [showPaste, setShowPaste] = useState(false)
  const [closing, setClosing] = useState(false)

  const loadReceipt = () => {
    api.getReceipt().then(setReceipt)
  }

  useEffect(loadReceipt, [])

  const handlePasteSubmit = async () => {
    if (!pasteText.trim()) return
    setUploading(true)
    setUploadResult(null)
    const result = await api.uploadReceipt('text', pasteText.trim())
    setUploadResult(result)
    setUploading(false)
    if (result.ok) {
      loadReceipt()
      setShowPaste(false)
      setPasteText('')
    }
  }

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setUploadResult(null)

    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch('/api/receipt/upload-file', {
        method: 'POST',
        body: formData,
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const result = await res.json()
      setUploadResult(result)
      if (result.ok) loadReceipt()
    } catch (err) {
      setUploadResult({ ok: false, error: err.message })
    }
    setUploading(false)
    e.target.value = '' // reset file input
  }

  const handleResolve = async (name, status) => {
    await api.resolveReceiptItem(name, status)
    loadReceipt()
  }

  const handleClose = async () => {
    setClosing(true)
    await api.closeReceipt()
    loadReceipt()
    setClosing(false)
  }

  const handleCloseNoReceipt = async () => {
    setClosing(true)
    await api.closeNoReceipt()
    loadReceipt()
    setClosing(false)
  }

  if (!receipt) return <div className="loading">Checking the tab...</div>

  if (!receipt.has_trip) {
    return (
      <>
        <div className="page-header">
          <h2 className="screen-heading">Receipt</h2>
          <div className="screen-sub">Reconcile what was purchased</div>
        </div>
        <div className="empty-state">
          <div className="icon">{'\u{1F9FE}'}</div>
          <p>No active trip to reconcile. Build a list and start shopping first.</p>
        </div>
      </>
    )
  }

  const hasAnyActivity = receipt.has_ordered || receipt.has_checked
  const hasReconciled = receipt.matched.length > 0 || receipt.substituted.length > 0 || receipt.not_fulfilled.length > 0
  const allResolved = receipt.unresolved.length === 0 && hasReconciled

  return (
    <>
      <div className="page-header">
        <h2 className="screen-heading">Receipt</h2>
        <div className="screen-sub">
          {hasReconciled
            ? `${receipt.matched.length} matched, ${receipt.substituted.length} substituted, ${receipt.not_fulfilled.length} not fulfilled`
            : 'Reconcile what was purchased'}
        </div>
      </div>

      {/* Upload section — show if no receipt yet */}
      {!receipt.has_receipt && hasAnyActivity && (
        <div className="receipt-upload">
          <div className="receipt-upload-label">Upload your receipt</div>
          <div className="receipt-upload-actions">
            <label className="receipt-btn">
              Upload PDF or image
              <input
                type="file"
                accept=".pdf,.jpg,.jpeg,.png,.webp,.eml"
                onChange={handleFileUpload}
                style={{ display: 'none' }}
              />
            </label>
            <button className="receipt-btn" onClick={() => setShowPaste(!showPaste)}>
              Paste receipt text
            </button>
          </div>

          {showPaste && (
            <div className="receipt-paste">
              <textarea
                className="receipt-textarea"
                placeholder="Paste your receipt email, order confirmation, or item list here..."
                value={pasteText}
                onChange={e => setPasteText(e.target.value)}
                rows={8}
              />
              <button
                className="build-list-btn"
                onClick={handlePasteSubmit}
                disabled={uploading || !pasteText.trim()}
              >
                {uploading ? 'Parsing...' : 'Parse Receipt'}
              </button>
            </div>
          )}

          {uploadResult && !uploadResult.ok && (
            <div className="submit-error">{uploadResult.error}</div>
          )}
          {uploadResult && uploadResult.ok && (
            <div className="submit-success">
              Matched {uploadResult.matched} item{uploadResult.matched !== 1 ? 's' : ''}
            </div>
          )}
        </div>
      )}

      {/* Unresolved items — need receipt */}
      {!hasReconciled && receipt.unresolved.length > 0 && (
        <div className="receipt-section">
          <div className="receipt-section-label">
            Awaiting receipt ({receipt.unresolved.length} item{receipt.unresolved.length !== 1 ? 's' : ''})
          </div>
          {receipt.unresolved.map(item => (
            <div key={item.name} className="receipt-item unresolved">
              <div className="receipt-item-name">{item.name}</div>
              <div className="receipt-item-meta">
                {item.ordered ? 'Ordered' : 'Checked off'}
                {item.product_price && ` \u00B7 ${formatPrice(item.product_price)}`}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Matched items */}
      {receipt.matched.length > 0 && (
        <div className="receipt-section">
          <div className="receipt-section-label matched-label">
            Matched ({receipt.matched.length})
          </div>
          {receipt.matched.map(item => (
            <div key={item.name} className="receipt-item matched">
              <div className="receipt-item-check">{'\u2713'}</div>
              <div className="receipt-item-info">
                <div className="receipt-item-name">{item.name}</div>
                {item.receipt_item && item.receipt_item !== item.name && (
                  <div className="receipt-item-detail">{item.receipt_item}</div>
                )}
                {item.receipt_price != null && (
                  <div className="receipt-item-price">{formatPrice(item.receipt_price)}</div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Substituted items */}
      {receipt.substituted.length > 0 && (
        <div className="receipt-section">
          <div className="receipt-section-label substituted-label">
            Substituted ({receipt.substituted.length})
          </div>
          {receipt.substituted.map(item => (
            <div key={item.name} className="receipt-item substituted">
              <div className="receipt-item-info">
                <div className="receipt-item-name">{item.name}</div>
                {item.receipt_item && (
                  <div className="receipt-item-detail">
                    Received: {item.receipt_item}
                    {item.receipt_price != null && ` \u00B7 ${formatPrice(item.receipt_price)}`}
                  </div>
                )}
              </div>
              <div className="receipt-item-actions">
                <button
                  className="receipt-resolve-btn accept"
                  onClick={() => handleResolve(item.name, 'matched')}
                >
                  That's fine
                </button>
                <button
                  className="receipt-resolve-btn flag"
                  onClick={() => handleResolve(item.name, 'not_fulfilled')}
                >
                  Note for next time
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Not fulfilled items */}
      {receipt.not_fulfilled.length > 0 && (
        <div className="receipt-section">
          <div className="receipt-section-label not-fulfilled-label">
            Not fulfilled ({receipt.not_fulfilled.length})
          </div>
          {receipt.not_fulfilled.map(item => (
            <div key={item.name} className="receipt-item not-fulfilled">
              <div className="receipt-item-name">{item.name}</div>
              <div className="receipt-item-meta">Will carry over to next list</div>
            </div>
          ))}
        </div>
      )}

      {/* Close actions */}
      {hasAnyActivity && (
        <div className="receipt-footer">
          {allResolved ? (
            <button
              className="build-list-btn"
              onClick={handleClose}
              disabled={closing}
            >
              {closing ? 'Closing...' : `Close Receipt ${'\u2713'}`}
            </button>
          ) : (
            <button
              className="receipt-close-quiet"
              onClick={handleCloseNoReceipt}
              disabled={closing}
            >
              {closing ? 'Closing...' : 'No receipt \u2014 close anyway'}
            </button>
          )}
        </div>
      )}
    </>
  )
}

function formatPrice(price) {
  if (price == null) return ''
  return `$${price.toFixed(2)}`
}
