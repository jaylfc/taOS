### Fixed
- PWA manifest (`manifest-desktop.json`) and every icon it references are now individually exempt from the auth middleware, so Chrome on Android can install taOS as a PWA without 401 errors on the manifest or icon fetches
