### Fixed
- APNs push payloads no longer let a caller-supplied `data["image"]` silently replace the explicit `image` argument: explicit arguments now win over `data`, and `aps.mutable-content` is set from the image that actually lands in the payload, so the notification service extension never fetches an image the flag was not computed for (tsk-674fwg).
- `aps.mutable-content` likewise follows an action set supplied through `data`, so decision buttons threaded that way are no longer dropped by the extension.
- A stray `data["aps"]` can no longer overwrite the `aps` envelope built from the explicit push arguments.
