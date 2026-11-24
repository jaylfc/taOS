### Fixed

- Signing in to a taOS account from a device no longer signs you out of the device or fails with "Unexpected response from the account service". The account service's session cookie shared a name with the device's own session cookie, so signing in replaced the device session and the follow-up request lost its cookie. Account cookies are now kept under their own `taosmy_` name, and only those are sent to the account service. A device that was already caught by this simply shows the normal local login once.
- The account proxy now logs the upstream status and content type for each call (never bodies, query strings or cookies).
- Your taOS account session cookie is never sent to apps, agents or shortcuts behind the proxies, and they cannot set it. Apps also no longer have their cookies merged into one broken header when they set several at once.
