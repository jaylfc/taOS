### Fixed
- APNs push payloads no longer let a caller-supplied `data["image"]` silently replace the explicit `image` argument: explicit arguments now win over `data`, and `aps.mutable-content` is set from the image that actually lands in the payload, so the notification service extension never fetches an image the flag was not computed for (tsk-674fwg).
- `aps.mutable-content` likewise follows an action set supplied through `data`, so decision buttons threaded that way are no longer dropped by the extension.
- A stray `data["aps"]` can no longer overwrite the `aps` envelope built from the explicit push arguments.
- An explicit `actions=[]` argument now takes precedence over a stale `data["actions"]` list, instead of being treated as omitted and silently overridden.
- An explicit `actions` argument (including `[]`) now also overrides `payload["actions"]` itself, not only the `aps.mutable-content` gate, so the stale `data`-supplied action set can no longer leak into the payload the client actually receives.

### Docs
- Fixed a stale docstring on `notifications_push._build_payload` that claimed the service worker only reads `event.notification.data`; it now explains why `image` is also copied to the top level.
