### Fixed

- Torrent settings non-admin test now submits a value that differs from the default to properly verify settings are unchanged on 403.
- Collab A2 handshake test correctly asserts that repeat-accept retains the original endpoint when `keep_existing_endpoints=True`.
- Orb catalog changelog fragment clarified: expiry validation runs after signature and serial format verification; error wording excludes null from the expires condition.
- BlueZ agent pin-code timeout test now asserts `org.bluez.Error.Rejected` (the actual timeout error) instead of `org.bluez.Error.Canceled`.
- Hardware WiFi routes now have integration tests verifying non-admin requests return 403 for all write endpoints (enabled, disconnect, connect, scan, forget).
- DeerFlow bridge test restores the shared `_SEMAPHORE` and `handle_user_message` fixtures after modification.
- Anthropic gateway test covers non-space whitespace-only image URLs (tabs, newlines, form feeds, etc.) in addition to spaces.
- Check-manifests test asserts the "more warnings hidden" notice is printed when warning output is capped.
- Check-manifests test adds coverage for manifests with only `min_context` or only `max_context` (single context field) — no warning should be emitted.
- Models recommendation test uses a fallback variant that is not the first variant in the list.
- Providers test verifies the canonical URL is passed to model discovery and checks the saved provider configuration.
- Memory remember tests assert the complete agent index path (not just suffix) for dbPath in the ingest payload.