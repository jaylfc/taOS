### Fixed

- Threads the request's User-Agent header into every auth session validation caller, including the `get_current_user` dependency and all `session_user` calls in routes (chat, store, etc.). This fixes browser users who were rejected with 401 errors after the User-Agent hash validation hardening in #3120.
- Added `session_user_for_request` and `validate_session_for_request` helper methods in `tinyagentos/auth.py` to read User-Agent from requests.
- Updated all `get_user`, `validate_session`, and `session_user` calls in routes to include the `user_agent` parameter.
- Updated all test mocks to accept the `user_agent=None` parameter.

### Added

- RED-FIRST test `TestSessionUserAgentBindingRED.test_browser_user_agent_binding_fixes_auth_endpoints` that logs in through the real login route with a User-Agent header, verifies that protected endpoints accept the session when the User-Agent matches, and controls that a different User-Agent is properly rejected.

### Changed

- `tests/test_memory_model.py::TestPutMemoryModel::test_put_forbidden_for_non_admin` - Updated monkeypatched `session_user` lambda to accept `user_agent=None` parameter
- `tests/test_routes_cluster_pairing.py::test_pending_non_admin_gets_403` and `test_confirm_non_admin_gets_403` - Updated monkeypatched `session_user` lambda to accept `user_agent=None` parameter
- `tests/test_taos_agent_config.py::TestAdminGate` - Updated all 4 test methods to accept `user_agent=None` parameter
- `tinyagentos/auth.py` - Added `session_user_for_request` and `validate_session_for_request` helper methods, updated `get_current_user` and `get_user` to pass user_agent parameter
- `tinyagentos/routes/auth.py` - Updated `get_user` call to include user_agent parameter
- `tinyagentos/routes/chat.py` - Updated all 4 `session_user` calls to include user_agent from request headers
- `tinyagentos/routes/chat_unified_bus_view.py` - Updated `session_user` call to include user_agent from request headers
- `tinyagentos/routes/store_install.py` - Updated `_get_current_user` helper to include user_agent from request headers
- `tinyagentos/routes/taosgo.py` - Updated `validate_session` call to include user_agent from request headers