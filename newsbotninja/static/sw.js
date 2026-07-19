/* Newsbotninja 🥷 — Service Worker for Web Push */

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', function (event) {
  let data = {
    title: 'Newsbotninja 🥷',
    body: 'New trending stories are in!',
    url: '/',
    icon: 'https://placehold.co/192x192/14b8a6/0d1117?text=🥷',
  };
  try { data = Object.assign(data, event.data.json()); } catch (e) {}

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body:    data.body,
      icon:    data.icon,
      badge:   data.icon,
      data:    { url: data.url },
      vibrate: [200, 100, 200],
      tag:     'newsbotninja-push',      // replaces previous notification
      renotify: false,
    })
  );
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  const target = event.notification.data?.url || '/';
  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then(function (clientList) {
        for (const client of clientList) {
          if ('focus' in client) return client.focus();
        }
        if (self.clients.openWindow) return self.clients.openWindow(target);
      })
  );
});
