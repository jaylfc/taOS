### Fixed

- Fixed sharing grant tests in `tests/test_sharing_routes.py` and `tests/test_sharing_grants.py` that built a hand-made `AsyncClient` with an unbound `csrf_token = secrets.token_hex(32)`; dev's `verify_csrf` now requires the token to be bound to the session, so these tests use `bound_csrf_token(app, session)`. No production change.
