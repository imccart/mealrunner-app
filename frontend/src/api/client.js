const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const api = {
  // Meals
  getMeals: () => request('/meals'),
  getPastMeals: () => request('/meals/past'),
  swapMeal: (date) => request(`/meals/${date}/swap`, { method: 'POST' }),
  swapMealSmart: (date, body = {}) => request(`/meals/${date}/swap-smart`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  swapSide: (date) => request(`/meals/${date}/swap-side`, { method: 'POST' }),
  getSides: (date) => request(`/meals/${date}/sides`),
  setSide: (date, side) => request(`/meals/${date}/set-side`, {
    method: 'POST',
    body: JSON.stringify({ side }),
  }),
  toggleGrocery: (date) => request(`/meals/${date}/toggle-grocery`, { method: 'POST' }),
  setMeal: (date, recipeId) => request(`/meals/${date}/set`, {
    method: 'POST',
    body: JSON.stringify({ recipe_id: recipeId }),
  }),
  suggestMeals: () => request('/meals/suggest', { method: 'POST' }),
  allToGrocery: () => request('/meals/all-to-grocery', { method: 'POST' }),
  swapDays: (dateA, dateB) => request('/meals/swap-days', {
    method: 'POST',
    body: JSON.stringify({ date_a: dateA, date_b: dateB }),
  }),
  removeMeal: (date) => request(`/meals/${date}`, { method: 'DELETE' }),
  setFreeform: (date, name) => request(`/meals/${date}/set-freeform`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  }),
  getCandidates: (date) => request(`/meals/${date}/candidates`),

  // Order
  getOrder: () => request('/order'),
  searchProducts: (itemName) => request(`/order/search/${encodeURIComponent(itemName)}`),
  selectProduct: (itemName, product) => request('/order/select', {
    method: 'POST',
    body: JSON.stringify({ item_name: itemName, product }),
  }),
  deselectProduct: (itemName) => request(`/order/deselect/${encodeURIComponent(itemName)}`, { method: 'POST' }),
  submitOrder: () => request('/order/submit', { method: 'POST' }),

  // Grocery
  getGrocery: () => request('/grocery'),
  addGroceryItem: (name) => request('/grocery/add', {
    method: 'POST',
    body: JSON.stringify({ name }),
  }),
  toggleGroceryItem: (name) => request(`/grocery/toggle/${encodeURIComponent(name)}`, { method: 'POST' }),
  getGrocerySuggestions: () => request('/grocery/suggestions'),
  getGroceryTrips: () => request('/grocery/trips'),
  getCarryover: () => request('/grocery/carryover'),
  buildMyList: (carryover = []) => request('/grocery/build', {
    method: 'POST',
    body: JSON.stringify({ carryover }),
  }),

  // Receipt
  getReceipt: () => request('/receipt'),
  uploadReceipt: (type, content) => request('/receipt/upload', {
    method: 'POST',
    body: JSON.stringify({ type, content }),
  }),
  resolveReceiptItem: (name, status) => request('/receipt/resolve', {
    method: 'POST',
    body: JSON.stringify({ name, status }),
  }),
  closeReceipt: () => request('/receipt/close', { method: 'POST' }),
  closeNoReceipt: () => request('/receipt/close-no-receipt', { method: 'POST' }),

  // Regulars
  getRegulars: () => request('/regulars'),
  addRegular: (name, shoppingGroup, storePref) => request('/regulars', {
    method: 'POST',
    body: JSON.stringify({ name, shopping_group: shoppingGroup || '', store_pref: storePref || 'either' }),
  }),
  toggleRegular: (id) => request(`/regulars/${id}/toggle`, { method: 'POST' }),
  removeRegular: (name) => request(`/regulars/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  // Recipes
  getRecipes: () => request('/recipes'),
}
