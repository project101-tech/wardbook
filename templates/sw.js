/* Wardbook service worker: shows push notifications when the site is closed, and opens the right page on tap. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

function pathOf(u) { try { return new URL(u, self.location.origin).pathname; } catch (e) { return ''; } }

async function show(d) {
  var url = new URL(d.url || '/', self.location.origin).href;
  var wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });

  // Already looking at that exact page? The page shows it live, so skip the pop-up (Chrome allows this
  // only when a visible, focused window exists, which is exactly what we test for).
  var watching = wins.some(function (w) { return w.visibilityState === 'visible' && w.focused && pathOf(w.url) === pathOf(url); });
  if (watching) { wins.forEach(function (w) { w.postMessage({ type: 'push', data: d }); }); return; }

  var title = d.title || 'Wardbook', body = d.body || '', count = 1, baseTitle = title;
  var tag = d.tag || ('wb-' + Date.now());

  // Several messages from the same chat collapse into one notification: "Rahul (3 new messages)", showing the latest.
  if (d.collapse && d.tag) {
    var old = await self.registration.getNotifications({ tag: d.tag });
    if (old.length) {
      var prev = old[0].data || {};
      count = (prev.count || 1) + 1;
      baseTitle = prev.baseTitle || title;
      title = baseTitle + ' (' + count + ' new messages)';
    }
  }

  await self.registration.showNotification(title, {
    body: body,
    icon: d.icon || undefined,
    badge: d.badge || undefined,
    tag: tag,
    renotify: true,               // buzz again even though the tag matches an earlier one
    vibrate: [120, 60, 120],
    timestamp: d.ts || Date.now(),
    data: { url: url, count: count, baseTitle: baseTitle }
  });
}

self.addEventListener('push', function (event) {
  var d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { body: event.data ? event.data.text() : '' }; }
  event.waitUntil(show(d));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async function () {
    var wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (var i = 0; i < wins.length; i++) {
      var w = wins[i];
      if (new URL(w.url).origin !== self.location.origin) continue;
      try {
        await w.focus();
        if (w.url !== url) await w.navigate(url);
        return;
      } catch (e) { /* this window can't be navigated: try the next one, then open a new one */ }
    }
    await self.clients.openWindow(url);
  })());
});
