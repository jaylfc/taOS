### Fixed

- Fixed `test_device_member_with_write_is_refused_like_a_session` to use a real browser session (`taos_session` cookie) for the member and POST to `/api/projects/shared/files/upload`, asserting the exact 404 refusal. The prior test hit a nonexistent `/api/sessions/upload` route with the device bearer and accepted `in (403, 404)`, so it never exercised the session upload path.