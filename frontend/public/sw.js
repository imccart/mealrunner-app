const CACHE_NAME = 'mealrunner-v1'
const API_CACHE = 'mealrunner-api-v1'

// API paths to cache for offline use
const CACHED_API_PATHS = ['/api/grocery', '/api/meals', '/api/auth/me']

self.addEventListener('install', (event) => {
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    // Clear static cache on every SW update to pick up new deploys
    caches.delete(CACHE_NAME)
      .then(() => caches.keys())
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== CACHE_NAME && k !== API_CACHE)
            .map((k) => caches.delete(k))
        )
      )
      .then(() => caches.open(CACHE_NAME))
      .then(async (cache) => {
        try {
          const response = await fetch('/app')
          if (response.ok) {
            const html = await response.clone().text()
            await cache.put(new Request('/app'), response)
            const assets = [...html.matchAll(/["'](\/assets\/[^"']+)["']/g)].map(m => m[1])
            if (assets.length) await cache.addAll(assets)
          }
        } catch (e) { /* offline during activation */ }
      })
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)

  // API requests: network first, cache fallback
  if (CACHED_API_PATHS.some((p) => url.pathname === p)) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const clone = response.clone()
            caches.open(API_CACHE).then((cache) => cache.put(event.request, clone))
          }
          return response
        })
        .catch(() => caches.match(event.request))
    )
    return
  }

  // App shell HTML: network first, cache fallback (so deploys always get fresh code)
  if (url.pathname === '/app' || url.pathname.startsWith('/app/')) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const clone = response.clone()
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone))
          }
          return response
        })
        .catch(() => caches.match(event.request))
    )
    return
  }

  // Hashed assets (/assets/): cache first (filenames change on deploy)
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached
        return fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone()
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone))
          }
          return response
        })
      })
    )
    return
  }

  // Icons, manifest, favicon: cache first
  if (url.pathname.match(/^\/(favicon\.ico|icon-\d+\.png|apple-touch-icon\.png|manifest\.json)$/)) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached
        return fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone()
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone))
          }
          return response
        })
      })
    )
    return
  }
})
