### Fixed

- BrowserApp web push (`routes/desktop_browser/push.py`) now converts the stored VAPID PEM to the base64url-DER signing key pywebpush expects, reusing the `_vapid_signing_key` helper from `notifications_push`. Previously the raw PEM reached `pywebpush.webpush`, py_vapid rejected it with "Could not deserialize key data", and every BrowserApp push send failed silently. An unusable key is now logged as an error once per send instead of being swallowed per subscription.
