import { useState, useEffect, useRef } from 'react'
import { api } from '../api/client'
import Sheet from './Sheet'
import FeedbackFab from './FeedbackFab'
import styles from './OrderPage.module.css'

// Render an item's display name with the user's chosen grocery-list quantity
// appended ("apples x 4"). The Kroger-cart quantity (item.product.quantity) is
// what actually goes into the order; the grocery quantity is a hint to the
// user about how many they want total. They might satisfy 4 apples by picking
// 4 of one product, or 2+2 across SKUs.
const displayName = (item) =>
  item && item.quantity > 1 ? `${item.name} x ${item.quantity}` : item?.name || ''

const NOVA_LABELS = {
  1: 'Minimally processed',
  2: 'Processed ingredient',
  3: 'Processed',
  4: 'Ultra-processed',
}

function ProductTransparency({ nova, nutriscore, brand, parentCompany, violations, onTapUnknown }) {
  const [showInfo, setShowInfo] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const novaText = nova ? `${nova}, ${NOVA_LABELS[nova]}` : 'Unknown'
  const nutriText = nutriscore ? nutriscore.toUpperCase() : 'Unknown'
  const parentUnknown = !parentCompany || parentCompany === "We're not sure"
  const parentText = parentUnknown ? 'Unknown' : parentCompany

  const v = violations || null
  const haveRecallData = v !== null && typeof v.fda_total_recalls === 'number'
  const totalRecalls = haveRecallData ? v.fda_total_recalls : null
  const seriousRecalls = haveRecallData ? (v.fda_class_i || 0) : 0
  // Inline summary on the Parent sub-line. 0 IS data — surface it as a positive signal.
  let recallSummary = null
  if (haveRecallData) {
    if (totalRecalls === 0) recallSummary = '0 FDA recalls'
    else if (seriousRecalls > 0) recallSummary = `${totalRecalls} FDA recalls (${seriousRecalls} serious)`
    else recallSummary = `${totalRecalls} FDA recall${totalRecalls === 1 ? '' : 's'}`
  }
  // Expandable details only when there's more to show (recent date, breakdown).
  const parentExpandable = !parentUnknown && totalRecalls > 0

  return (
    <div className={styles.productTransparency}>
      <button
        className={styles.transparencyInfoDot}
        onClick={(e) => { e.stopPropagation(); setShowInfo(!showInfo) }}
        title="What are these?"
      >{'\u24D8'}</button>

      <div className={styles.transparencyRow}>
        <span className={styles.transparencyLabel}>NOVA</span>
        <span className={`${styles.transparencyValue} ${nova ? styles[`nova${nova}`] : styles.transparencyUnknown}`}>
          {novaText}
        </span>
      </div>

      <div className={styles.transparencyRow}>
        <span className={styles.transparencyLabel}>Nutri-Score</span>
        <span className={`${styles.transparencyValue} ${nutriscore ? styles[`nutri${nutriscore.toUpperCase()}`] : styles.transparencyUnknown}`}>
          {nutriText}
        </span>
      </div>

      <div className={styles.transparencyRow}>
        <span className={styles.transparencyLabel}>Parent</span>
        <div className={styles.transparencyValueCol}>
          <span
            className={`${styles.transparencyValue} ${parentUnknown ? styles.transparencyUnknown : ''} ${parentUnknown || parentExpandable ? styles.transparencyValueTappable : ''}`}
            onClick={
              parentUnknown
                ? (e) => { e.stopPropagation(); e.preventDefault(); onTapUnknown(brand) }
                : parentExpandable
                ? (e) => { e.stopPropagation(); e.preventDefault(); setExpanded(!expanded) }
                : undefined
            }
          >
            {parentText}
            {parentExpandable && (
              <span className={styles.transparencyChevron}>{expanded ? '\u25B4' : '\u25BE'}</span>
            )}
          </span>
          {recallSummary && (
            <span className={`${styles.transparencyRecallSummary} ${totalRecalls === 0 ? styles.transparencyRecallClean : (seriousRecalls > 0 ? styles.transparencyRecallSerious : styles.transparencyRecallSome)}`}>
              {recallSummary}
            </span>
          )}
        </div>
      </div>

      {showInfo && (
        <div className={styles.transparencyLegend} onClick={(e) => e.stopPropagation()}>
          <div className={styles.transparencyLegendItem}>
            <strong>NOVA:</strong> how processed the food is (1 = minimal, 4 = ultra-processed)
          </div>
          <div className={styles.transparencyLegendItem}>
            <strong>Nutri-Score:</strong> nutritional grade, A (best) through E
          </div>
          <div className={styles.transparencyLegendItem}>
            <strong>Parent:</strong> the company that owns this brand
          </div>
          <div className={styles.transparencyLegendSource}>
            NOVA + Nutri-Score from Open Food Facts
          </div>
        </div>
      )}

      {expanded && parentExpandable && (
        <div className={styles.companyDetails} onClick={(e) => e.stopPropagation()}>
          <div className={styles.companyDetailsRow}>
            <span className={styles.companyDetailsLabel}>FDA food recalls</span>
            <span className={styles.companyDetailsValue}>{v.fda_total_recalls}</span>
          </div>
          {v.fda_class_i > 0 && (
            <div className={styles.companyDetailsRow}>
              <span className={styles.companyDetailsLabel}>Class I (serious)</span>
              <span className={styles.companyDetailsValue}>{v.fda_class_i}</span>
            </div>
          )}
          {v.fda_most_recent && (
            <div className={styles.companyDetailsRow}>
              <span className={styles.companyDetailsLabel}>Most recent</span>
              <span className={styles.companyDetailsValue}>{v.fda_most_recent.slice(0, 4)}-{v.fda_most_recent.slice(4, 6)}-{v.fda_most_recent.slice(6, 8)}</span>
            </div>
          )}
          <div className={styles.companyDetailsSource}>Source: FDA openFDA</div>
        </div>
      )}
    </div>
  )
}

// ── Sort logic for search results ───────────────────────────────────
// Backend already filters out off-topic matches with a proximity check; we
// just re-order what's left here. Sort is purely client-side so toggling
// between pills doesn't re-query Kroger.

const MR_WEIGHTS = {
  price: 0.25,
  unitPrice: 0.15,
  deal: 0.15,
  nova: 0.20,
  nutri: 0.10,
  rep: 0.15,
}
const NUTRI_VALUES = { A: 1.0, B: 0.75, C: 0.5, D: 0.25, E: 0.0 }
const NOVA_VALUES = { 1: 1.0, 2: 0.67, 3: 0.33, 4: 0.0 }

function effectivePrice(p) {
  return p.promo_price != null ? p.promo_price : p.price
}

function computeMrRank(products) {
  // Normalize each axis 0-1 within the result set (relative scoring) so a
  // search with an expensive ceiling doesn't punish "best within this set".
  const prices = products.map(effectivePrice).filter(v => v != null && v > 0)
  const unitPrices = products.map(p => p.unit_price).filter(v => v != null && v > 0)
  const minPrice = prices.length ? Math.min(...prices) : null
  const maxPrice = prices.length ? Math.max(...prices) : null
  const minUnit = unitPrices.length ? Math.min(...unitPrices) : null
  const maxUnit = unitPrices.length ? Math.max(...unitPrices) : null

  return products.map(p => {
    const axes = {}
    const ep = effectivePrice(p)
    if (ep != null && maxPrice != null && maxPrice > minPrice) {
      axes.price = 1 - (ep - minPrice) / (maxPrice - minPrice)
    } else if (ep != null && maxPrice === minPrice) {
      axes.price = 1
    }
    if (p.unit_price != null && maxUnit != null && maxUnit > minUnit) {
      axes.unitPrice = 1 - (p.unit_price - minUnit) / (maxUnit - minUnit)
    } else if (p.unit_price != null && maxUnit === minUnit) {
      axes.unitPrice = 1
    }
    if (p.baseline_price && ep != null && p.baseline_price > 0) {
      const ratio = (p.baseline_price - ep) / p.baseline_price
      axes.deal = Math.max(0, Math.min(1, ratio))
    }
    if (p.nova && NOVA_VALUES[p.nova] != null) {
      axes.nova = NOVA_VALUES[p.nova]
    }
    if (p.nutriscore && NUTRI_VALUES[p.nutriscore.toUpperCase()] != null) {
      axes.nutri = NUTRI_VALUES[p.nutriscore.toUpperCase()]
    }
    if (p.violations && typeof p.violations.fda_total_recalls === 'number') {
      const total = p.violations.fda_total_recalls
      const serious = p.violations.fda_class_i || 0
      const penalty = Math.min(1, (total / 30) * 0.7 + (serious / 5) * 0.3)
      axes.rep = 1 - penalty
    }
    // Reallocate weights of missing axes proportionally across present ones
    // so a product with missing NOVA isn't penalised vs one with NOVA = 1.
    const present = Object.keys(axes)
    if (present.length === 0) return { ...p, _mrRank: 0 }
    const totalPresentWeight = present.reduce((s, k) => s + MR_WEIGHTS[k], 0)
    const score = present.reduce(
      (s, k) => s + (MR_WEIGHTS[k] / totalPresentWeight) * axes[k],
      0,
    )
    return { ...p, _mrRank: score }
  })
}

function sortProducts(products, mode) {
  if (!products || products.length === 0) return products
  const arr = [...products]
  if (mode === 'price') {
    return arr.sort((a, b) => {
      const ea = effectivePrice(a), eb = effectivePrice(b)
      if (ea == null && eb == null) return 0
      if (ea == null) return 1
      if (eb == null) return -1
      return ea - eb
    })
  }
  if (mode === 'unit') {
    return arr.sort((a, b) => {
      if (a.unit_price == null && b.unit_price == null) return 0
      if (a.unit_price == null) return 1
      if (b.unit_price == null) return -1
      return a.unit_price - b.unit_price
    })
  }
  if (mode === 'deal') {
    return arr.sort((a, b) => {
      const da = a.baseline_price && effectivePrice(a) != null ? a.baseline_price - effectivePrice(a) : null
      const db = b.baseline_price && effectivePrice(b) != null ? b.baseline_price - effectivePrice(b) : null
      if (da == null && db == null) return 0
      if (da == null) return 1
      if (db == null) return -1
      return db - da  // largest deal first
    })
  }
  if (mode === 'mr') {
    return computeMrRank(arr).sort((a, b) => b._mrRank - a._mrRank)
  }
  return arr  // mode null/unknown → preserve Kroger's order
}

function formatPrice(price) {
  if (price == null) return ''
  return `$${price.toFixed(2)}`
}

export default function OrderPage() {
  const [order, setOrder] = useState(null)
  const [activeItem, setActiveItem] = useState(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [products, setProducts] = useState(null)
  const [sortMode, setSortMode] = useState(null) // null = Kroger default order
  const [searching, setSearching] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [pendingProduct, setPendingProduct] = useState(null) // product awaiting quantity confirmation
  const [pendingQty, setPendingQty] = useState(1)
  const [showAnythingElse, setShowAnythingElse] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitResult, setSubmitResult] = useState(null)
  const [krogerAccounts, setKrogerAccounts] = useState(null)
  const [selectedAccount, setSelectedAccount] = useState(null)
  const [fulfillment, setFulfillment] = useState(() => localStorage.getItem('mealrunner_fulfillment') || 'curbside')
  const [storeInfo, setStoreInfo] = useState(null)
  const [showQueue, setShowQueue] = useState(false)
  const [mobileSection, setMobileSection] = useState(null) // 'ordered' | 'elsewhere' | null
  const [communityBrand, setCommunityBrand] = useState(null)
  const [communityValue, setCommunityValue] = useState('')
  // Session-scoped skip list. Items added via the right-arrow / next gesture
  // stay in `pending` but auto-advance won't land on them again. Cleared by
  // a full page reload or by explicitly tapping the item in the queue.
  const [skipped, setSkipped] = useState(() => new Set())
  const [communityConfirm, setCommunityConfirm] = useState(false)
  const [noStore, setNoStore] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const [comparisons, setComparisons] = useState(null)
  const [showComparison, setShowComparison] = useState(false)
  const [showSendSheet, setShowSendSheet] = useState(false)
  const [showCompareSheet, setShowCompareSheet] = useState(false)

  const [sharedAccountName, setSharedAccountName] = useState(null)

  const activeItemRef = useRef(null)

  // When activeItem changes (prev/next nav, queue tap), scroll the new item's
  // section to the top of the viewport. Without this, the page stays scrolled
  // wherever the prior item left it, which lands mid-list for items whose
  // product results are shorter than the previous item's scroll depth.
  useEffect(() => {
    if (!activeItem || !activeItemRef.current) return
    activeItemRef.current.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [activeItem])

  useEffect(() => {
    api.getKrogerHouseholdAccounts().then(data => {
      const accounts = data.accounts || []
      setKrogerAccounts(accounts)
      const yours = accounts.find(a => a.is_you)
      if (yours) setSelectedAccount(yours.user_id)
      else if (accounts.length > 0) {
        setSelectedAccount(accounts[0].user_id)
        setSharedAccountName(accounts[0].display_name)
      }
    }).catch(() => setKrogerAccounts([]))
    api.getKrogerLocation().then(data => setStoreInfo(data)).catch(() => {})
  }, [])

  useEffect(() => {
    api.getOrder().then(data => {
      setOrder(data)
      setLoadError(false)
      if (data.pending.length > 0 && !activeItem) {
        setActiveItem(data.pending[0].name)
      }
    }).catch(() => setLoadError(true))
    // Periodic refresh to pick up background price updates
    const interval = setInterval(() => {
      api.getOrder().then(data => setOrder(data)).catch(() => {})
    }, 10000)
    return () => clearInterval(interval)
  }, [])

  const doSearch = (term) => {
    if (!term) { setProducts(null); return }
    setSearching(true)
    setProducts(null)
    setNoStore(false)
    api.searchProducts(term, fulfillment).then(data => {
      if (data.error === 'no_store') {
        setNoStore(true)
        setSearching(false)
        return
      }
      setProducts(data)
      setSearching(false)
    }).catch(err => {
      console.error('Search failed:', err)
      setSearching(false)
    })
  }

  const loadMore = () => {
    if (!products || !products.has_more || loadingMore) return
    const nextStart = (products.start || 1) + products.products.length
    setLoadingMore(true)
    api.searchProducts(searchTerm, fulfillment, nextStart).then(data => {
      setProducts(prev => ({
        ...prev,
        products: [...prev.products, ...data.products],
        start: data.start,
        has_more: data.has_more,
      }))
      setLoadingMore(false)
    }).catch(() => setLoadingMore(false))
  }

  useEffect(() => {
    if (activeItem) {
      setSearchTerm(activeItem)
      doSearch(activeItem)
    }
  }, [activeItem, fulfillment])

  // Fetch price comparisons when selected items change
  const selectedCount = order?.selected?.length || 0
  useEffect(() => {
    if (selectedCount === 0) { setComparisons(null); return }
    const timer = setTimeout(() => {
      api.getPriceComparison().then(data => setComparisons(data.comparisons)).catch(() => {})
    }, 2000)
    return () => clearTimeout(timer)
  }, [selectedCount])

  const storeName = storeInfo?.name || 'Kroger'
  const activeItemData = order ? [...order.pending, ...order.selected, ...order.buy_elsewhere].find(i => i.name === activeItem) : null

  const advanceToNext = (updatedOrder) => {
    const pending = updatedOrder.pending
    if (pending.length === 0) {
      setActiveItem(null)
      return
    }
    // Stay on current if still pending (e.g., after "anything else? yes")
    const currentStillPending = pending.find(p => p.name === activeItem)
    if (currentStillPending) return

    // Advance to the next pending item AFTER the current position, wrapping around.
    // Skipped names are excluded so the right-arrow gesture sticks within
    // the session. If every remaining pending item is skipped, drop to the
    // end-state instead of forcing the user back through skipped items.
    const allNames = [...updatedOrder.pending.map(p => p.name), ...updatedOrder.selected.map(s => s.name)]
    const curIdx = allNames.indexOf(activeItem)
    const pendingSet = new Set(pending.map(p => p.name))
    const eligible = (name) => pendingSet.has(name) && !skipped.has(name)
    // Look forward from current position
    for (let i = curIdx + 1; i < allNames.length; i++) {
      if (eligible(allNames[i])) {
        setActiveItem(allNames[i])
        return
      }
    }
    // Wrap around from the beginning
    for (let i = 0; i < curIdx; i++) {
      if (eligible(allNames[i])) {
        setActiveItem(allNames[i])
        return
      }
    }
    // Every remaining pending item is skipped — show end-state.
    setActiveItem(null)
  }

  const handleSelect = (product) => {
    setPendingProduct(product)
    setPendingQty(1)
    setShowAnythingElse(false)
  }

  const handleConfirmQuantity = async () => {
    if (!pendingProduct) return
    try {
      const data = await api.selectProduct(activeItem, pendingProduct, pendingQty)
      setOrder(data)
      setPendingProduct(null)
      setShowAnythingElse(true)
    } catch { /* silent */ }
  }

  const handleAnythingElseYes = () => {
    setShowAnythingElse(false)
    // Keep activeItem the same, search stays visible
  }

  const handleAnythingElseNo = () => {
    setShowAnythingElse(false)
    // Use functional state access to get the latest order
    setOrder(current => {
      if (current) advanceToNext(current)
      return current
    })
  }

  const handleBuyElsewhere = async () => {
    if (!activeItem || !activeItemData) return
    try {
      const data = await api.buyElsewhere(activeItemData.id)
      setOrder(data)
      advanceToNext(data)
    } catch { /* silent */ }
  }

  const handleUndoBuyElsewhere = async (item) => {
    try {
      const data = await api.buyElsewhere(item.id) // toggles off
      setOrder(data)
      setActiveItem(item.name)
      setMobileSection(null)
    } catch { /* silent */ }
  }

  // Grocery-level actions — mark item on the trip and remove from order
  const handleGroceryAction = async (action) => {
    if (!activeItem || !activeItemData) return
    try {
      if (action === 'bought') await api.toggleGroceryItem(activeItemData.id)
      else if (action === 'have_it') await api.haveItGroceryItem(activeItemData.id)
      else if (action === 'remove') await api.removeGroceryItem(activeItemData.id)
      const data = await api.getOrder()
      setOrder(data)
      advanceToNext(data)
    } catch { /* silent */ }
  }

  // Nav arrows iterate pending items only. Selected items are revisited
  // by tapping them in the queue/sidebar, not by stepping past the end of
  // pending. Past the last pending, → drops into the end-state panel.
  const handlePrev = () => {
    if (!activeItem || !order) return
    const pendingNames = order.pending.map(p => p.name)
    if (pendingNames.length === 0) return
    const idx = pendingNames.indexOf(activeItem)
    if (idx === -1) {
      setActiveItem(pendingNames[pendingNames.length - 1])
      return
    }
    if (idx === 0) return
    setActiveItem(pendingNames[idx - 1])
  }

  const handleNext = () => {
    if (!activeItem || !order) return
    const pendingNames = order.pending.map(p => p.name)
    if (pendingNames.length === 0) {
      setActiveItem(null)
      return
    }
    // Right-arrow = "skip for now". Record the current item so neither this
    // handler nor advanceToNext lands on it again this session.
    const justSkipped = activeItem
    const nextSkipped = new Set(skipped)
    if (pendingNames.includes(justSkipped)) nextSkipped.add(justSkipped)
    setSkipped(nextSkipped)

    const idx = pendingNames.indexOf(activeItem)
    // Walk forward from idx looking for the next non-skipped pending item.
    for (let i = idx + 1; i < pendingNames.length; i++) {
      if (!nextSkipped.has(pendingNames[i])) {
        setActiveItem(pendingNames[i])
        return
      }
    }
    // No more eligible items after current — drop to end-state.
    setActiveItem(null)
  }

  const handleKeepShopping = () => {
    if (!order) return
    // Explicit "keep shopping" tap = the user wants to see what's left,
    // including anything they skipped earlier. Clear the session skip list.
    setSkipped(new Set())
    const pending = order.pending
    if (pending.length > 0) {
      setActiveItem(pending[0].name)
    } else {
      // All picked — wrap to first item
      const allNames = [...order.pending.map(p => p.name), ...order.selected.map(s => s.name)]
      if (allNames.length > 0) setActiveItem(allNames[0])
    }
  }

  const handleDeselect = async (itemName) => {
    try {
      const data = await api.deselectProduct(itemName)
      setOrder(data)
      setActiveItem(itemName)
    } catch { /* silent */ }
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const result = await api.submitOrder(selectedAccount || undefined)
      setSubmitResult(result)
      if (result.ok) {
        // Refresh order — submitted items will be filtered out
        const data = await api.getOrder()
        setOrder(data)
        setActiveItem(null)
      }
    } catch {
      setSubmitResult({ ok: false, error: 'Failed to submit order' })
    }
    setSubmitting(false)
  }

  if (loadError) return (
    <>
      <div className="page-header">
        <h2 className="screen-heading">Order</h2>
      </div>
      <div className="empty-state">
        <div className="icon">{'\u{1F6D2}'}</div>
        <p>Couldn't reach the kitchen. Check your connection and try again.</p>
      </div>
      <FeedbackFab page="order" />
    </>
  )

  if (!order) return <><div className="loading">Prepping...</div><FeedbackFab page="order" /></>

  const allItems = [...order.pending, ...order.selected]
  const elsewhereItems = order.buy_elsewhere || []
  const pickedCount = order.selected.length
  const pendingCount = order.pending.length
  const elsewhereCount = elsewhereItems.length
  const totalCount = allItems.length

  if (totalCount === 0) {
    return (
      <>
        <div className="page-header">
          <h2 className="screen-heading">Order</h2>
          <div className="screen-sub">Select products for your list</div>
        </div>
        <div className="empty-state">
          <div className="icon">{'\u{1F6D2}'}</div>
          <p>No unchecked items to order.</p>
        </div>
        <FeedbackFab page="order" />
      </>
    )
  }

  const storeDetails = (
    <div className={styles.storeDetails}>
      <div className={styles.storeDetailsRow}>
        <div className={styles.storeDetailsName}>
          <select className={styles.storeSelect} value="kroger" disabled>
            <option value="kroger">{storeName}</option>
          </select>
        </div>
        <div className={styles.fulfillmentToggle}>
          <button
            className={`${styles.fulfillmentBtn}${fulfillment === 'curbside' ? ` ${styles.active}` : ''}`}
            onClick={() => { setFulfillment('curbside'); localStorage.setItem('mealrunner_fulfillment', 'curbside') }}
          >Pickup</button>
          <button
            className={`${styles.fulfillmentBtn}${fulfillment === 'delivery' ? ` ${styles.active}` : ''}`}
            onClick={() => { setFulfillment('delivery'); localStorage.setItem('mealrunner_fulfillment', 'delivery') }}
          >Delivery</button>
        </div>
      </div>
      {storeInfo?.address && (
        <div className={styles.storeDetailsAddress}>{storeInfo.address}</div>
      )}
      {sharedAccountName && (
        <div className={styles.storeDetailsShared}>Ordering through {sharedAccountName}'s account</div>
      )}
    </div>
  )

  // Mobile header counts
  const mobileHeaderCounts = (
    <div className={styles.orderMobileCounts}>
      <button
        className={`${styles.orderCountBtn}${!mobileSection ? ` ${styles.active}` : ''}`}
        onClick={() => setMobileSection(null)}
      >
        {pendingCount} left
      </button>
      <span className={styles.orderCountDot}>{'\u00B7'}</span>
      <button
        className={`${styles.orderCountBtn}${mobileSection === 'ordered' ? ` ${styles.active}` : ''}`}
        onClick={() => setMobileSection(mobileSection === 'ordered' ? null : 'ordered')}
      >
        {pickedCount} ordered
      </button>
      {elsewhereCount > 0 && (
        <>
          <span className={styles.orderCountDot}>{'\u00B7'}</span>
          <button
            className={`${styles.orderCountBtn}${mobileSection === 'elsewhere' ? ` ${styles.active}` : ''}`}
            onClick={() => setMobileSection(mobileSection === 'elsewhere' ? null : 'elsewhere')}
          >
            {elsewhereCount} elsewhere
          </button>
        </>
      )}
    </div>
  )

  // Mobile collapsed queue row
  const mobileQueueRow = activeItem ? (
    <div className={styles.pickingRow}>
      <button className={styles.pickingRowNav} onClick={handlePrev}>{'\u2190'}</button>
      <div className={styles.pickingRowMain} onClick={() => setShowQueue(true)}>
        <span className={styles.pickingRowLabel}>Picking for</span>
        <span className={styles.pickingRowItem}>{displayName(activeItemData) || activeItem}</span>
        <span className={styles.pickingRowExpand}>{'\u25BE'}</span>
      </div>
      <button className={styles.pickingRowNav} onClick={handleNext} title="Next item">{'\u2192'}</button>
      <button className={styles.pickingRowDone} onClick={() => setActiveItem(null)} title="Done picking">Done</button>
    </div>
  ) : (
    <div className={`${styles.pickingRow} ${styles.done}`}>
      <div className={styles.pickingRowMain}>
        <span className={styles.pickingRowSummary}>
          {pickedCount} of {totalCount} picked
          {order.total_price > 0 && ` \u00B7 ${formatPrice(order.total_price)}`}
        </span>
      </div>
      {submitResult?.ok && (
        <span className={styles.pickingRowSent}>Sent {'\u2713'}</span>
      )}
    </div>
  )

  const queuePanel = (
    <div className={styles.orderQueuePanel}>
      <div className={styles.orderQueueHeader}>
        <div className={styles.orderQueueTitle}>Items</div>
        <div className={styles.orderQueueSub}>
          {pendingCount > 0
            ? `${pendingCount} left to pick`
            : 'All items selected'}
        </div>
      </div>
      <div className={styles.orderQueueList}>
        {order.pending.length > 0 && (
          <>
            <div className={styles.queueSectionLabel}>Active</div>
            {order.pending.map(item => {
              const isActive = item.name === activeItem
              return (
                <button
                  key={item.name}
                  className={`${styles.queueItem}${isActive ? ` ${styles.active}` : ''}`}
                  onClick={() => setActiveItem(item.name)}
                >
                  <span className={styles.queueItemName}>{displayName(item)}</span>
                  {item.for_meals?.length > 0 && (
                    <span className={styles.queueItemMeals}>{item.for_meals.join(', ')}</span>
                  )}
                </button>
              )
            })}
          </>
        )}
        {order.selected.length > 0 && (
          <>
            <div className={styles.queueSectionLabel}>Ordered</div>
            {order.selected.map(item => (
              <button
                key={item.name}
                className={`${styles.queueItem} ${styles.selected}`}
                onClick={() => handleDeselect(item.name)}
              >
                <span className={styles.queueItemName}>{displayName(item)}</span>
                <span className={styles.queueCheck}>{'\u2713'}</span>
              </button>
            ))}
          </>
        )}
        {elsewhereItems.length > 0 && (
          <>
            <div className={styles.queueSectionLabel}>Buying elsewhere</div>
            {elsewhereItems.map(item => (
              <button
                key={item.name}
                className={`${styles.queueItem} ${styles.elsewhere}`}
                onClick={() => handleUndoBuyElsewhere(item)}
                title="Bring back to ordering"
              >
                <span className={styles.queueItemName}>{displayName(item)}</span>
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  )

  const centerPanel = (
    <div className={styles.orderCenterPanel}>
      <div className={styles.orderDesktopStoreDetails}>
        {storeDetails}
      </div>
      {activeItem && (
        <div className={styles.orderActiveItem} ref={activeItemRef}>
          <div className={styles.orderItemTopRow}>
            <div>
              <div className={styles.orderItemLabel}>Picking for</div>
              <div className={styles.orderItemName}>{displayName(activeItemData) || activeItem}</div>
              {activeItemData?.for_meals?.length > 0 && (
                <div className={styles.orderItemMeals}>{activeItemData.for_meals.join(', ')}</div>
              )}
              {activeItemData?.notes && (
                <div className={styles.orderItemNote}>{activeItemData.notes}</div>
              )}
            </div>
          </div>
          <form className={styles.orderSearchForm} onSubmit={e => {
            e.preventDefault()
            doSearch(searchTerm)
            e.target.querySelector('input')?.blur()
          }}>
            <input
              className={styles.orderSearchInput}
              type="search"
              enterKeyHint="search"
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
              placeholder="Search products..."
            />
            {searchTerm !== activeItem && (
              <button type="button" className={styles.orderSearchReset} onClick={() => {
                setSearchTerm(activeItem)
                doSearch(activeItem)
              }} title="Reset search">{'\u21BA'}</button>
            )}
          </form>
        </div>
      )}

      {!activeItem && totalCount > 0 && !submitResult?.ok && (
        <div className={styles.orderEndState}>
          <div className={styles.orderEndSummary}>
            {pendingCount > 0
              ? `${pickedCount} of ${totalCount} items selected`
              : 'All items selected'}
            {order.total_price > 0 && ` \u00B7 ${formatPrice(order.total_price)}`}
          </div>
          <button className={styles.orderEndBtn} onClick={handleKeepShopping}>
            {pendingCount > 0 ? 'Keep shopping' : 'Review selections'}
          </button>
          <button className={`${styles.orderEndBtn} ${styles.orderEndPrimary}`} onClick={() => setShowSendSheet(true)}>
            Send to {storeName} {'\u2192'}
          </button>
          {comparisons && comparisons.length > 0 && (
            <button className={styles.orderEndBtn} onClick={() => setShowCompareSheet(true)}>
              Compare nearby stores
            </button>
          )}
          <div className={styles.orderEndDesktopHint}>
            You can also send your order from the panel on the right.
          </div>
        </div>
      )}

      {pendingProduct && (
        <div className={styles.orderModalOverlay} onClick={() => setPendingProduct(null)}>
          <div className={styles.orderModal} onClick={e => e.stopPropagation()}>
            <div className={styles.orderQtyLabel}>How many?</div>
            <div className={styles.orderQtyProduct}>{pendingProduct.name}</div>
            <div className={styles.orderQtyControls}>
              <button className={styles.orderQtyBtn} onClick={() => setPendingQty(q => Math.max(1, q - 1))}>{'\u2212'}</button>
              <span className="order-qty-value">{pendingQty}</span>
              <button className={styles.orderQtyBtn} onClick={() => setPendingQty(q => q + 1)}>+</button>
            </div>
            <button className={styles.orderQtyConfirm} onClick={handleConfirmQuantity}>Confirm</button>
          </div>
        </div>
      )}

      {showAnythingElse && (
        <div className={styles.orderModalOverlay}>
          <div className={styles.orderModal}>
            <span>Anything else for <strong>{activeItem}</strong>?</span>
            <div className={styles.orderAnythingElseBtns}>
              <button className={styles.orderGroceryBtn} onClick={handleAnythingElseYes}>Yes</button>
              <button className={styles.orderGroceryBtn} onClick={handleAnythingElseNo}>No</button>
            </div>
          </div>
        </div>
      )}

      {noStore && !searching && (
        <div className="empty-state" style={{ padding: '20px 16px' }}>
          <p>Set your store in Preferences to search products.</p>
        </div>
      )}

      {searching && <div className="loading">{
        ['Dicing...', 'Simmering...', 'Slicing...', "Cookin'...", 'Chopping...', 'Seasoning...'][
          (activeItem || '').length % 6
        ]
      }</div>}

      {activeItem && products && !searching && (
        <>
          {products.preferences.length > 0 && (
            <div className={styles.orderSection}>
              <div className={styles.orderSectionLabel}>Prior selections</div>
              {products.preferences.map(pref => (
                <div key={pref.upc} className={`${styles.productCard} ${styles.preference}${pref.in_stock === false ? ` ${styles.outOfStock}` : ''}`} style={{ position: 'relative' }}>
                  <button
                    className={styles.prefDismiss}
                    onClick={(e) => {
                      e.stopPropagation()
                      api.deletePreference(pref.upc).catch(() => {})
                      setProducts(prev => prev ? { ...prev, preferences: prev.preferences.filter(p => p.upc !== pref.upc) } : prev)
                    }}
                    title="Remove prior selection"
                  >{'\u00D7'}</button>
                  <button
                    className={styles.prefSelectBtn}
                    onClick={() => pref.in_stock !== false && handleSelect({
                      upc: pref.upc, name: pref.name,
                      brand: pref.brand, size: pref.size,
                      price: pref.promo_price || pref.price || null,
                      image: pref.image || '',
                    })}
                    disabled={pref.in_stock === false}
                  >
                    {pref.image && (
                      <div className={styles.productImage}>
                        <img src={pref.image} alt="" loading="lazy" />
                      </div>
                    )}
                    <div className={styles.productInfo}>
                      <div className={styles.productName}>
                        {pref.name}
                        {pref.rating === 1 && <span className={styles.prefStar}> {'\u{1F44D}'}</span>}
                        {pref.rating === -1 && <span className={styles.prefDown}> {'\u{1F44E}'}</span>}
                      </div>
                      <div className={styles.productMeta}>
                        {pref.brand && <span>{pref.brand}</span>}
                        {pref.size && <span> {'\u00B7'} {pref.size}</span>}
                        {(pref.price || pref.promo_price) && (
                          <>
                            <span> {'\u00B7'} </span>
                            {pref.promo_price ? (
                              <>
                                <span className={styles.pricePromo}>{formatPrice(pref.promo_price)}</span>
                                <span className={styles.priceOriginal}> {formatPrice(pref.price)}</span>
                              </>
                            ) : (
                              <span className={styles.price}>{formatPrice(pref.price)}</span>
                            )}
                          </>
                        )}
                      </div>
                      {pref.in_stock === false && <div className={styles.outOfStockLabel}>{pref.unavailable_reason || 'Unavailable'}</div>}
                      <ProductTransparency
                        nova={pref.nova}
                        nutriscore={pref.nutriscore}
                        brand={pref.brand}
                        parentCompany={pref.parent_company}
                        violations={pref.violations}
                        onTapUnknown={(b) => setCommunityBrand(b || 'Unknown')}
                      />
                    </div>
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className={styles.orderSection}>
            <div className={styles.orderSectionLabel}>
              {storeName} results
              {products.search_term !== activeItem && (
                <span className={styles.searchTermNote}> for "{products.search_term}"</span>
              )}
            </div>
            {products.products.length > 0 && (
              <div className={styles.sortPills}>
                {[
                  { key: 'price', label: 'Price' },
                  { key: 'unit', label: 'Per Unit' },
                  { key: 'deal', label: 'Deal' },
                  { key: 'mr', label: 'MR Rank' },
                ].map(opt => (
                  <button
                    key={opt.key}
                    className={`${styles.sortPill}${sortMode === opt.key ? ` ${styles.sortPillActive}` : ''}`}
                    onClick={() => setSortMode(sortMode === opt.key ? null : opt.key)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
            {products.products.length === 0 ? (
              <div className="empty-state">
                <p>No products found.</p>
              </div>
            ) : (
              <div className={styles.productList}>
                {sortProducts(products.products, sortMode).map(p => {
                  const effectivePrice = p.promo_price || p.price
                  // 5% threshold so cents-of-noise don't flip the badge on/off
                  const belowUsual = p.baseline_price && effectivePrice && effectivePrice < p.baseline_price * 0.95
                  return (
                  <button
                    key={p.upc}
                    className={`${styles.productRow}${!p.in_stock ? ` ${styles.outOfStock}` : ''}`}
                    onClick={() => p.in_stock && handleSelect({
                      upc: p.upc, name: p.name,
                      brand: p.brand, size: p.size,
                      price: effectivePrice,
                      image: p.image,
                    })}
                    disabled={!p.in_stock}
                  >
                    <div className={styles.productRowThumb}>
                      {p.image
                        ? <img src={p.image} alt="" loading="lazy" />
                        : <div className={styles.productRowThumbBlank}></div>}
                    </div>
                    <div className={styles.productRowMain}>
                      {p.brand && <div className={styles.productRowBrand}>{p.brand}</div>}
                      <div className={styles.productRowName}>{p.name}</div>
                      <div className={styles.productRowPrices}>
                        <span className={styles.priceCurrent}>{formatPrice(effectivePrice)}</span>
                        {p.promo_price && (
                          <span className={styles.priceStruck}>{formatPrice(p.price)}</span>
                        )}
                        {belowUsual && (
                          <span className={styles.priceDealBadge}>Below usual</span>
                        )}
                        {p.rating === 1 && <span className={styles.prefStar}>{'\u{1F44D}'}</span>}
                        {p.rating === -1 && <span className={styles.prefDown}>{'\u{1F44E}'}</span>}
                      </div>
                      <div className={styles.productRowRefs}>
                        {p.baseline_price && (
                          <span className={styles.priceRef}>Usually {formatPrice(p.baseline_price)}</span>
                        )}
                        {p.unit_price && p.unit_label && (
                          <span className={styles.priceUnit}>{formatPrice(p.unit_price)}{p.unit_label}</span>
                        )}
                        {p.size && (
                          <span className={styles.productRowSize}>{p.size}</span>
                        )}
                      </div>
                      <ProductTransparency
                        nova={p.nova}
                        nutriscore={p.nutriscore}
                        brand={p.brand}
                        parentCompany={p.parent_company}
                        violations={p.violations}
                        onTapUnknown={(b) => setCommunityBrand(b || 'Unknown')}
                      />
                      {!p.in_stock && <div className={styles.outOfStockLabel}>Unavailable</div>}
                    </div>
                  </button>
                )})}
              </div>
            )}
            {products.has_more && (
              <button className={styles.loadMoreBtn} onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? 'Loading...' : 'More results'}
              </button>
            )}
          </div>
        </>
      )}

    </div>
  )

  const summaryPanel = (
    <div className={styles.orderSummaryPanel}>
      <div className={styles.orderSummaryHeader}>
        <div className={styles.orderSummaryTitle}>Order Summary</div>
        <div className={styles.orderSummarySub}>
          {pickedCount} of {totalCount} items selected
        </div>
      </div>
      <div className={styles.orderSummaryScroll}>
        {pickedCount > 0 ? (
          <>
            <div className={styles.orderSummaryListLabel}>Selected so far</div>
            {(() => {
              const nameCounts = {}
              order.selected.forEach(i => { nameCounts[i.name] = (nameCounts[i.name] || 0) + 1 })
              return order.selected.map((item, idx) => (
              <div key={item.product?.upc || `sel-${idx}`} className={styles.orderSummaryRow}>
                <span className={styles.orderSummaryItemName}>{nameCounts[item.name] > 1 ? (item.product?.name || item.name) : item.name}</span>
                <span className={styles.orderSummaryItemPrice}>
                  {item.product?.price ? (
                    (item.product.quantity || 1) > 1
                      ? `${formatPrice(item.product.price)} \u00D7 ${item.product.quantity}`
                      : formatPrice(item.product.price)
                  ) : ''}
                </span>
              </div>
            ))})()}
            {activeItem && order.pending.some(p => p.name === activeItem) && (
              <div className={`${styles.orderSummaryRow} ${styles.selecting}`}>
                <span className={styles.orderSummaryItemName}>{displayName(activeItemData) || activeItem}</span>
                <span className={styles.orderSummaryItemSelecting}>selecting...</span>
              </div>
            )}
            <div className={styles.orderSummaryTotal}>
              <span>Est. subtotal</span>
              <strong>{formatPrice(order.total_price)}</strong>
            </div>
            {comparisons && comparisons.length > 0 && (
              <div className={styles.priceComparisonPanel}>
                <button className={styles.comparisonToggle} onClick={() => setShowComparison(!showComparison)}>
                  Compare nearby stores {showComparison ? '\u25B4' : '\u25BE'}
                </button>
                {showComparison && (
                  <>
                    {comparisons.map(c => (
                      <div key={c.location_id} className={styles.comparisonRow}>
                        <div className={styles.comparisonStore}>{c.name}</div>
                        <div className={c.savings > 0 ? styles.comparisonSavings : styles.comparisonMore}>
                          {c.savings > 0
                            ? `Save $${c.savings.toFixed(2)}`
                            : c.savings === 0
                            ? 'Same price'
                            : `$${Math.abs(c.savings).toFixed(2)} more`}
                          <span className={styles.comparisonDetail}>
                            {' '}(comparing {c.items_compared} of {c.items_total} items)
                          </span>
                        </div>
                      </div>
                    ))}
                    <div className={styles.comparisonDisclaimer}>
                      Prices are estimates and may change. Not all items could be compared.
                      To save, change your store in the Kroger app at checkout.
                    </div>
                  </>
                )}
              </div>
            )}
          </>
        ) : (
          <div className={styles.orderSummaryEmpty}>
            No products selected yet. Pick items from the search results.
          </div>
        )}
      </div>
      <div className={styles.orderSummaryFooter}>
        {pickedCount > 0 && (
          <>
            {krogerAccounts && krogerAccounts.length === 0 ? (
              <div className={styles.submitHint}>Connect your account in Preferences, or ask a household member to share access</div>
            ) : (
              <>
                {krogerAccounts && krogerAccounts.length > 1 && (
                  <div className={styles.accountPicker}>
                    <label className={styles.accountPickerLabel}>Submit as</label>
                    <select
                      className={styles.accountPickerSelect}
                      value={selectedAccount || ''}
                      onChange={e => setSelectedAccount(e.target.value)}
                    >
                      {krogerAccounts.map(a => (
                        <option key={a.user_id} value={a.user_id}>
                          {a.display_name}{a.is_you ? ' (you)' : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {submitResult?.ok ? (
                  <div className="submit-success">Sent to {storeName} {'\u2713'}</div>
                ) : (
                  <button
                    className={styles.orderFinalizeBtn}
                    onClick={handleSubmit}
                    disabled={submitting}
                  >
                    {submitting ? 'Sending...' : `Send to ${storeName} ${'\u2192'}`}
                  </button>
                )}
                {submitResult && !submitResult.ok && (
                  <div className="submit-error">{submitResult.error}</div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )

  return (
    <>
      {/* Mobile header */}
      <div className={`page-header ${styles.orderMobileHeader}`}>
        <h2 className="screen-heading">Order</h2>
      </div>

      {/* Mobile: store details */}
      <div className={styles.orderMobileStoreDetails}>
        {storeDetails}
      </div>

      {/* Mobile: header counts */}
      <div className={styles.orderMobileQueueRow}>
        {mobileHeaderCounts}
      </div>

      {/* Mobile: collapsed queue row */}
      <div className={styles.orderMobileQueueRow}>
        {mobileQueueRow}
      </div>

      {/* Mobile: queue sheet */}
      {showQueue && (
        <Sheet onClose={() => setShowQueue(false)}>
          <div className="sheet-title">Items to pick</div>
          <div className="sheet-sub">{pickedCount} of {totalCount} selected</div>
          <div className={styles.queueSheetList}>
            {order.pending.length > 0 && (
              <>
                <div className={styles.queueSheetSection}>Active</div>
                {order.pending.map(item => (
                  <button
                    key={item.name}
                    className={`${styles.queueSheetItem}${item.name === activeItem ? ` ${styles.active}` : ''}`}
                    onClick={() => {
                      // Explicit re-engagement clears any prior skip for this name.
                      if (skipped.has(item.name)) {
                        const next = new Set(skipped)
                        next.delete(item.name)
                        setSkipped(next)
                      }
                      setActiveItem(item.name)
                      setShowQueue(false)
                      setMobileSection(null)
                    }}
                  >
                    <span>{displayName(item)}</span>
                  </button>
                ))}
              </>
            )}
            {order.selected.length > 0 && (
              <>
                <div className={styles.queueSheetSection}>Ordered</div>
                {order.selected.map(item => (
                  <button
                    key={item.name}
                    className={`${styles.queueSheetItem} ${styles.selected}`}
                    onClick={() => { handleDeselect(item.name); setShowQueue(false); setMobileSection(null) }}
                  >
                    <span>{displayName(item)}</span>
                    <span className={styles.queueCheck}>{'\u2713'}</span>
                  </button>
                ))}
              </>
            )}
            {elsewhereItems.length > 0 && (
              <>
                <div className={styles.queueSheetSection}>Buying elsewhere</div>
                {elsewhereItems.map(item => (
                  <button
                    key={item.name}
                    className={`${styles.queueSheetItem} ${styles.elsewhere}`}
                    onClick={() => { handleUndoBuyElsewhere(item); setShowQueue(false) }}
                  >
                    <span>{displayName(item)}</span>
                    <span className={styles.queueSheetElsewhere}>elsewhere</span>
                  </button>
                ))}
              </>
            )}
          </div>
        </Sheet>
      )}

      {/* Desktop 3-column layout */}
      <div className={styles.orderDesktopLayout}>
        {queuePanel}
        {centerPanel}
        {summaryPanel}
      </div>

      {/* Mobile: section views (when tapping ordered/elsewhere counts) */}
      {mobileSection === 'ordered' && (
        <div className={styles.orderMobileContent}>
          <div className={styles.orderMobileSection}>
            <div className={styles.orderMobileSectionTitle}>Ordered ({pickedCount})</div>
            {order.selected.map(item => (
              <button
                key={item.name}
                className={`${styles.queueSheetItem} ${styles.selected}`}
                onClick={() => { handleDeselect(item.name); setMobileSection(null) }}
              >
                <span>{displayName(item)}</span>
                <span className={styles.queueCheck}>{'\u2713'}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {mobileSection === 'elsewhere' && (
        <div className={styles.orderMobileContent}>
          <div className={styles.orderMobileSection}>
            <div className={styles.orderMobileSectionTitle}>Buying elsewhere ({elsewhereCount})</div>
            {elsewhereItems.map(item => (
              <button
                key={item.name}
                className={`${styles.queueSheetItem} ${styles.elsewhere}`}
                onClick={() => handleUndoBuyElsewhere(item)}
              >
                <span>{displayName(item)}</span>
                <span className={styles.queueSheetElsewhere}>tap to bring back</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Mobile: center content inline */}
      {!mobileSection && (
        <div className={styles.orderMobileContent}>
          {centerPanel}
        </div>
      )}

      {communityBrand && !communityConfirm && (
        <Sheet onClose={() => { setCommunityBrand(null); setCommunityValue('') }}>
          <div className="sheet-title">Who makes this?</div>
          <div className="sheet-sub">Help us fill in the gaps.</div>
          <div className={styles.communityForm}>
            <div className={styles.communityBrand}>Brand: <strong>{communityBrand}</strong></div>
            <input
              className={styles.communityInput}
              type="text"
              placeholder="e.g. General Mills"
              value={communityValue}
              onChange={(e) => setCommunityValue(e.target.value)}
              autoFocus
            />
            <button
              className="btn primary"
              disabled={!communityValue.trim()}
              onClick={async () => {
                await api.submitCommunityData('brand_ownership', communityBrand, communityValue.trim())
                setCommunityBrand(null)
                setCommunityValue('')
                setCommunityConfirm(true)
                setTimeout(() => setCommunityConfirm(false), 2000)
              }}
            >Submit</button>
          </div>
        </Sheet>
      )}
      {communityConfirm && (
        <div className={styles.communityToast}>Yes, Chef!</div>
      )}
      {showSendSheet && (
        <Sheet onClose={() => setShowSendSheet(false)}>
          <div className="sheet-title">Send to {storeName}</div>
          {pickedCount > 0 && (
            <div className="sheet-sub">{pickedCount} item{pickedCount !== 1 ? 's' : ''} selected {'\u00B7'} {formatPrice(order.total_price)} est.</div>
          )}
          <div className={styles.sendSheetContent}>
            {krogerAccounts && krogerAccounts.length === 0 ? (
              <div className={styles.submitHint}>Connect your account in Preferences, or ask a household member to share access</div>
            ) : (
              <>
                {krogerAccounts && krogerAccounts.length > 1 && (
                  <div className={styles.accountPicker}>
                    <label className={styles.accountPickerLabel}>Submit as</label>
                    <select
                      className={styles.accountPickerSelect}
                      value={selectedAccount || ''}
                      onChange={e => setSelectedAccount(e.target.value)}
                    >
                      {krogerAccounts.map(a => (
                        <option key={a.user_id} value={a.user_id}>
                          {a.display_name}{a.is_you ? ' (you)' : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <button
                  className={styles.buildListBtn}
                  onClick={async () => {
                    await handleSubmit()
                    setShowSendSheet(false)
                  }}
                  disabled={submitting || pickedCount === 0}
                >
                  {submitting ? 'Sending...' : `Send to ${storeName} \u2192`}
                </button>
              </>
            )}
          </div>
        </Sheet>
      )}

      {showCompareSheet && comparisons && (
        <Sheet onClose={() => setShowCompareSheet(false)}>
          <div className="sheet-title">Compare nearby stores</div>
          <div className={styles.compareSheetContent}>
            {comparisons.map(c => (
              <div key={c.location_id} className={styles.comparisonRow}>
                <div className={styles.comparisonStore}>{c.name}</div>
                <div className={c.savings > 0 ? styles.comparisonSavings : styles.comparisonMore}>
                  {c.savings > 0
                    ? `Save $${c.savings.toFixed(2)}`
                    : c.savings === 0
                    ? 'Same price'
                    : `$${Math.abs(c.savings).toFixed(2)} more`}
                  <span className={styles.comparisonDetail}>
                    {' '}(comparing {c.items_compared} of {c.items_total} items)
                  </span>
                </div>
              </div>
            ))}
            <div className={styles.comparisonDisclaimer}>
              Prices are estimates and may change. Not all items could be compared.
              Switching stores changes your default Kroger location.
            </div>
          </div>
        </Sheet>
      )}

      <FeedbackFab page="order" />
    </>
  )
}
