const DIVCIBER_CACHE = 'divciber-static-v1';
const STATIC_ASSETS = [
    '/static/css/style.css',
    '/static/js/script.js',
    '/static/js/pwa.js',
    '/static/img/Divciber_logo.png',
    '/static/img/favicon_divciber.png',
    '/static/manifest.webmanifest'
];

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(DIVCIBER_CACHE)
            .then(function (cache) {
                return cache.addAll(STATIC_ASSETS);
            })
            .then(function () {
                return self.skipWaiting();
            })
    );
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys()
            .then(function (keys) {
                return Promise.all(keys.map(function (key) {
                    if (key !== DIVCIBER_CACHE) {
                        return caches.delete(key);
                    }
                    return Promise.resolve();
                }));
            })
            .then(function () {
                return self.clients.claim();
            })
    );
});

self.addEventListener('fetch', function (event) {
    const request = event.request;
    const url = new URL(request.url);

    if (request.method !== 'GET' || url.origin !== self.location.origin) {
        return;
    }

    if (!url.pathname.startsWith('/static/')) {
        return;
    }

    event.respondWith(
        caches.match(request).then(function (cachedResponse) {
            if (cachedResponse) {
                return cachedResponse;
            }
            return fetch(request);
        })
    );
});
