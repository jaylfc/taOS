### Fixed

- Web Studio's Share view shows a "publishing isn't available on this taOS yet" empty state when the publish endpoint answers 404 or 501, instead of a raw error.
- The Share view no longer flashes "Sign in to your taOS account" while the account is still loading, and says when the account service is unavailable.
- Copy link reports "Copied!" or "Copy failed" instead of silently doing nothing when the clipboard is unavailable.
- Publish and unpublish share one URL helper.
- Unpublish does not survive a reload until the site listing returns its binding, because the current SiteRow shape carries no binding or fqdn field to hydrate that state.
