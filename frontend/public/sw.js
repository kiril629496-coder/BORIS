/* BORIS PWA service worker: business data is always network-authoritative. */
const BORIS_SW_VERSION = "boris-pwa-v9-phone-offline-scope";
const OFFLINE_CACHE = "boris-pwa-static-v9";
const OFFLINE_URL = "/phone-offline.html";

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(OFFLINE_CACHE);
    await cache.add(new Request(OFFLINE_URL, { cache: "reload" }));
    // Emergency-safe rollout: activate this worker immediately. We intentionally
    // do not claim every existing tab below, so unrelated BORIS pages are not reloaded.
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => event.waitUntil((async () => {
  for (const key of await caches.keys()) {
    if (key.startsWith("boris-phone-static-") || (key.startsWith("boris-pwa-static-") && key !== OFFLINE_CACHE)) await caches.delete(key);
  }

  // Recover only stale onboarding tabs. A stale prerender can be visible while
  // its JS handler is already dead, so clicking buttons produces no HTTP request.
  // Navigating the onboarding document reloads fresh JS; server-side onboarding
  // status then redirects already-completed users out of the wizard.
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of windows) {
    try {
      const url = new URL(client.url);
      if (url.origin !== self.location.origin) continue;
      if (url.pathname === "/onboarding") {
        await client.navigate("/onboarding?sw_refresh=v7");
      } else if (url.pathname === "/messages/mop-training") {
        // A frontend release can leave an already-open training tab executing
        // the previous JS contract. Reload this one interactive page on SW
        // activation so its buttons and API contract cannot remain stale.
        const refreshed = new URL(client.url);
        refreshed.searchParams.set("sw_refresh", "v7");
        await client.navigate(refreshed.toString());
      }
    } catch (_) {}
  }
})()));

self.addEventListener("message", event => {
  if (event.data?.type === "BORIS_SKIP_WAITING") self.skipWaiting();
  if (event.data?.type === "BORIS_SW_VERSION") event.source?.postMessage({ type: "BORIS_SW_VERSION", version: BORIS_SW_VERSION });
});

self.addEventListener("push", event => {
  event.waitUntil((async () => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; }
    catch (_) { data = { body: event.data ? event.data.text() : "" }; }
    const title = String(data.title || "BORIS · новый статус лида");
    const body = String(data.body || "Откройте BORIS, чтобы посмотреть обновление.");
    const url = String(data.url || "/messages");
    await self.registration.showNotification(title, {
      body,
      icon: "/boris-icon-192.png",
      badge: "/boris-icon-192.png",
      tag: String(data.tag || ("boris-lead-" + Date.now())),
      renotify: true,
      data: { url },
    });
  })());
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil((async () => {
    const target = new URL(String(event.notification?.data?.url || "/messages"), self.location.origin).toString();
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      try {
        if (new URL(client.url).origin === self.location.origin) {
          await client.navigate(target);
          return client.focus();
        }
      } catch (_) {}
    }
    return self.clients.openWindow(target);
  })());
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/_next/webpack-hmr")) return;

  if (req.mode === "navigate") {
    // The BORIS Phone offline screen is only valid for BORIS Phone itself.
    // Do not replace /login, /dashboard or other BORIS pages with a phone
    // offline document after a transient navigation failure.
    const isPhoneNavigation = url.pathname === "/phone-app" || url.pathname.startsWith("/phone-app/");
    if (!isPhoneNavigation) return;
    event.respondWith(fetch(req).catch(async () => {
      const fallback = await caches.match(OFFLINE_URL);
      return fallback || Response.error();
    }));
  }
});
