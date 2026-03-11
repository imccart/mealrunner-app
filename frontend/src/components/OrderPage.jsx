import { useState, useEffect } from 'react'
import { api } from '../api/client'

function NovaBadge({ nova }) {
  if (!nova) return null
  const labels = { 1: 'Minimal processing', 2: 'Processed ingredient', 3: 'Processed', 4: 'Ultra-processed' }
  const cls = `nova-badge nova-${nova}`
  return <span className={cls}>NOVA {nova} {'\u00B7'} {labels[nova]}</span>
}

function NutriBadge({ grade }) {
  if (!grade) return null
  const cls = `nutri-badge nutri-${grade}`
  return <span className={cls}>Nutri-Score {grade.toUpperCase()}</span>
}

function ProductInsights({ nova, nutriscore }) {
  if (!nova && !nutriscore) return null
  return (
    <div className="product-insights">
      <NovaBadge nova={nova} />
      <NutriBadge grade={nutriscore} />
    </div>
  )
}

function formatPrice(price) {
  if (price == null) return ''
  return `$${price.toFixed(2)}`
}

export default function OrderPage() {
  const [order, setOrder] = useState(null)
  const [activeItem, setActiveItem] = useState(null)
  const [modifier, setModifier] = useState('')
  const [products, setProducts] = useState(null)
  const [searching, setSearching] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitResult, setSubmitResult] = useState(null)

  useEffect(() => {
    api.getOrder().then(data => {
      setOrder(data)
      // Auto-select first pending item
      if (data.pending.length > 0 && !activeItem) {
        setActiveItem(data.pending[0].name)
      }
    })
  }, [])

  const doSearch = (itemName, mod) => {
    if (!itemName) { setProducts(null); return }
    const term = mod ? `${mod} ${itemName}` : itemName
    setSearching(true)
    setProducts(null)
    api.searchProducts(term).then(data => {
      setProducts(data)
      setSearching(false)
    }).catch(err => {
      console.error('Search failed:', err)
      setSearching(false)
    })
  }

  // Auto-search when active item changes (reset modifier)
  useEffect(() => {
    setModifier('')
    doSearch(activeItem, '')
  }, [activeItem])

  const handleSelect = async (product) => {
    const data = await api.selectProduct(activeItem, product)
    setOrder(data)
    // Move to next pending item
    if (data.pending.length > 0) {
      setActiveItem(data.pending[0].name)
    } else {
      setActiveItem(null)
    }
  }

  const handleDeselect = async (itemName) => {
    const data = await api.deselectProduct(itemName)
    setOrder(data)
    setActiveItem(itemName)
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    const result = await api.submitOrder()
    setSubmitResult(result)
    setSubmitting(false)
  }

  if (!order) return <div className="loading">Prepping...</div>

  const allItems = [...order.pending, ...order.selected]

  if (allItems.length === 0) {
    return (
      <>
        <div className="page-header">
          <h2 className="screen-heading">Order</h2>
          <div className="screen-sub">Select Kroger products for your list</div>
        </div>
        <div className="empty-state">
          <div className="icon">{'\u{1F6D2}'}</div>
          <p>No unchecked items to order. Check off items you bought in-store on the Grocery tab first.</p>
        </div>
      </>
    )
  }

  return (
    <>
      <div className="page-header">
        <h2 className="screen-heading">Order</h2>
        <div className="screen-sub">
          {order.pending.length > 0
            ? `${order.pending.length} item${order.pending.length !== 1 ? 's' : ''} to pick`
            : 'All items selected'}
        </div>
      </div>

      {/* Item queue — horizontal strip on mobile, vertical on desktop */}
      <div className="order-queue">
        {allItems.map(item => {
          const isSelected = !!item.product
          const isActive = item.name === activeItem
          return (
            <button
              key={item.name}
              className={`queue-item ${isActive ? 'active' : ''} ${isSelected ? 'selected' : ''}`}
              onClick={() => isSelected ? handleDeselect(item.name) : setActiveItem(item.name)}
            >
              <span className="queue-item-name">{item.name}</span>
              {isSelected && <span className="queue-check">{'\u2713'}</span>}
            </button>
          )
        })}
      </div>

      {/* Product results area */}
      <div className="order-content">
        {activeItem && (
          <div className="order-active-item">
            <div className="order-item-label">Picking for</div>
            <div className="order-item-name">{activeItem}</div>
            <form className="modifier-form" onSubmit={e => {
              e.preventDefault()
              doSearch(activeItem, modifier)
            }}>
              <input
                className="modifier-input"
                type="text"
                placeholder="Refine... e.g. organic, low sodium"
                value={modifier}
                onChange={e => setModifier(e.target.value)}
              />
              {modifier && (
                <button type="button" className="modifier-clear" onClick={() => {
                  setModifier('')
                  doSearch(activeItem, '')
                }}>{'\u00D7'}</button>
              )}
            </form>
          </div>
        )}

        {searching && <div className="loading">{
          ['Dicing...', 'Simmering...', 'Slicing...', "Cookin'...", 'Chopping...', 'Seasoning...'][
            (activeItem || '').length % 6
          ]
        }</div>}

        {products && !searching && (
          <>
            {/* Preferences */}
            {products.preferences.length > 0 && (
              <div className="order-section">
                <div className="order-section-label">Prior selections</div>
                {products.preferences.map(pref => (
                  <button
                    key={pref.upc}
                    className="product-card preference"
                    onClick={() => handleSelect({
                      upc: pref.upc, name: pref.name,
                      brand: pref.brand, size: pref.size,
                      price: null, image: pref.image || '',
                    })}
                  >
                    {pref.image && (
                      <div className="product-image">
                        <img src={pref.image} alt="" loading="lazy" />
                      </div>
                    )}
                    <div className="product-info">
                      <div className="product-name">{pref.name}</div>
                      <div className="product-meta">{pref.size}</div>
                    </div>
                    {pref.rating === 1 && <span className="pref-star">{'\u2605'}</span>}
                  </button>
                ))}
              </div>
            )}

            {/* Search results */}
            <div className="order-section">
              <div className="order-section-label">
                Kroger results
                {products.search_term !== activeItem && (
                  <span className="search-term-note"> for "{products.search_term}"</span>
                )}
              </div>
              {products.products.length === 0 ? (
                <div className="empty-state">
                  <p>No products found.</p>
                </div>
              ) : (
                <div className="product-grid">
                  {products.products.map(p => (
                    <button
                      key={p.upc}
                      className={`product-card ${!p.in_stock ? 'out-of-stock' : ''}`}
                      onClick={() => p.in_stock && handleSelect({
                        upc: p.upc, name: p.name,
                        brand: p.brand, size: p.size,
                        price: p.promo_price || p.price,
                        image: p.image,
                      })}
                      disabled={!p.in_stock}
                    >
                      {p.image && (
                        <div className="product-image">
                          <img src={p.image} alt="" loading="lazy" />
                        </div>
                      )}
                      <div className="product-info">
                        <div className="product-name">{p.name}</div>
                        <div className="product-meta">
                          {p.brand && <span>{p.brand}</span>}
                          {p.size && <span> {'\u00B7'} {p.size}</span>}
                        </div>
                        <div className="product-price-row">
                          {p.promo_price ? (
                            <>
                              <span className="price-promo">{formatPrice(p.promo_price)}</span>
                              <span className="price-original">{formatPrice(p.price)}</span>
                            </>
                          ) : (
                            <span className="price">{formatPrice(p.price)}</span>
                          )}
                        </div>
                        {!p.in_stock && <div className="out-of-stock-label">Unavailable</div>}
                      </div>
                      <ProductInsights nova={p.nova} nutriscore={p.nutriscore} />
                    </button>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Order summary footer */}
      {order.selected.length > 0 && (
        <div className="order-footer">
          <div className="order-summary">
            <span>{order.total_items} item{order.total_items !== 1 ? 's' : ''}</span>
            {order.total_price > 0 && (
              <span> {'\u00B7'} {formatPrice(order.total_price)}</span>
            )}
          </div>
          {submitResult?.ok ? (
            <div className="submit-success">Added to Kroger cart {'\u2713'}</div>
          ) : (
            <button
              className="build-list-btn"
              onClick={handleSubmit}
              disabled={submitting}
            >
              {submitting ? 'Submitting...' : `Finalize on Kroger ${'\u2192'}`}
            </button>
          )}
          {submitResult && !submitResult.ok && (
            <div className="submit-error">{submitResult.error}</div>
          )}
        </div>
      )}
    </>
  )
}
