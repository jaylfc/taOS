### Fixed

- CSRF tokens are now bound to the session id via HMAC-SHA256 over `taos_session`, closing the cross-subdomain cookie-planting attack vector under the `{user}.taos.my` model. A token minted for session A is now rejected when presented alongside session B's cookies.
