(function () {
    'use strict';

    if (!('serviceWorker' in navigator)) {
        return;
    }

    window.addEventListener('load', function () {
        navigator.serviceWorker.register('/service-worker.js', { scope: '/' }).catch(function () {
            // PWA support is progressive enhancement; failures must not affect the app.
        });
    });
})();
