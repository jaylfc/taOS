### Fixed

- `tests/test_auth.py`: Fixed session validation to properly pass user-agent headers, resolving 403 Forbidden errors for legitimate admin users and restoring expected behavior for non-object JSON body validation tests
- `tinyagentos/auth.py`: Updated `session_user()` method to accept optional user_agent parameter for proper session validation
- `tinyagentos/routes/auth.py`: Updated `_require_admin()` and `_require_self()` functions to pass user-agent headers to session validation, fixing admin authorization issues
- `tests/test_auth.py`: Updated `auth_client` and `no_cookie_client` fixtures to initialize agent registry and grants stores, preventing "AgentRegistryStore not initialised" errors