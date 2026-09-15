const CACHE_NAME = 'pg-manager-v2';

// Install event: cache initial shell assets
self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(['/', '/manifest.json']))
  );
});

// Activate event: clear old cache versions (e.g., v1)
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch event: ignore WebSocket requests, serve cached app shell or fetch from network
self.addEventListener('fetch', (e) => {
  if (e.request.url.includes('/ws')) return;
  e.respondWith(
    caches.match(e.request).then((res) => res || fetch(e.request))
  );
});