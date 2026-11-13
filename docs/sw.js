const C = "kompas-v26";
self.addEventListener("install", e => { self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(k => Promise.all(k.filter(x => x !== C).map(x => caches.delete(x)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  const u = new URL(e.request.url); if (u.origin !== location.origin) return;
  e.respondWith(fetch(e.request, {cache: "no-cache"}).then(r => { const c = r.clone(); caches.open(C).then(x => x.put(e.request, c)); return r; }).catch(() => caches.match(e.request)));
});
const PUSHW = "https://kompas-robot.user-testingtomino39.workers.dev";
self.addEventListener("push", e => {
  e.waitUntil(fetch(PUSHW + "/push/last", {cache: "no-store"}).then(r => r.json()).catch(() => ({t: "Kompas klidu", b: "Mrkni do Deníku.", u: "journal.html"}))
    .then(m => self.registration.showNotification(m.t || "Kompas klidu", {body: m.b || "", icon: "icon-192.png", badge: "icon-192.png", tag: "kompas", data: {u: m.u || "journal.html"}})));
});
self.addEventListener("notificationclick", e => {
  e.notification.close(); const url = new URL((e.notification.data && e.notification.data.u) || "journal.html", self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(cs => { for (const c of cs) { if (c.url.startsWith(self.registration.scope) && "focus" in c) { c.navigate(url); return c.focus(); } } return self.clients.openWindow(url); }));
});
