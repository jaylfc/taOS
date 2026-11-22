### Fixed
- Per-person preferences fix-forward: broker `public_fallback_enabled` now receives the caller's user_id (signed-in user, or agent owner for agent calls); system namespaces (auto-update, gpg, setup, taos_agent, offline_model) route PUT/GET to the global scope with admin-only writes; taos_agent preferences use global scope consistently as a system agent; offline_model added to _SYSTEM_NAMESPACES so migration preserves its row; migration tested over existing store with all namespaces present.
- Added integration test verifying Settings > Updates toggle (PUT /api/preferences/auto-update) reaches the AutoUpdateService read path.
### Added
- Keenable broker search/fetch routes (POST /api/broker/keenable/search, POST /api/broker/keenable/fetch) behind active grants.
