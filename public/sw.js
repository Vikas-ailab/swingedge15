const CACHE = 'swingedge-static-v1';
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/','/manifest.webmanifest','/icon.svg']))));
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET' || new URL(e.request.url).pathname.startsWith('/analyze') || new URL(e.request.url).pathname.startsWith('/scan')) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
