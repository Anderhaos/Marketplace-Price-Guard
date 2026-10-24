// Cache only public icon; never cache seller pages or authenticated API responses.
const CACHE_NAME = "mpnosale-public-icons-v1";
const SAFE_PATHS = ["/favicon.svg"];
const SAFE_NOTIFICATION_TARGETS = ["/notifications/", "/advisor/", "/stores/"];
function safeNotificationPath(value) {
  return SAFE_NOTIFICATION_TARGETS.includes(value) ? value : "/notifications/";
}
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SAFE_PATHS)));
  self.skipWaiting();
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith("mpnosale-public-icons-") && key !== CACHE_NAME)
      .map(key => caches.delete(key))
  )));
  self.clients.claim();
});
self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin ||
      !SAFE_PATHS.includes(url.pathname)) return;
  event.respondWith(caches.match(request).then(cached => cached || fetch(request)));
});
self.addEventListener("push", event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) {}
  const title = String(payload.title || "NoSale").slice(0,180);
  const body = String(payload.body || "Есть новое событие").slice(0,400);
  // Notifications may deep-link only to a small explicit same-origin allowlist.
  event.waitUntil(self.registration.showNotification(title, {
    body, icon: "/favicon.svg", badge: "/favicon.svg",
    data: {url: safeNotificationPath(payload.url)}, renotify: false,
  }));
});
self.addEventListener("notificationclick", event => {
  const target = safeNotificationPath(event.notification.data?.url);
  event.notification.close();
  event.waitUntil(self.clients.matchAll({type:"window", includeUncontrolled:true}).then(windows => {
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin) {
        return client.focus().then(() => client.navigate(target));
      }
    }
    return self.clients.openWindow(target);
  }));
});
