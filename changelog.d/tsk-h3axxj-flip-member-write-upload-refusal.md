### Fixed

- Changed `test_device_can_upload_when_member_with_write` to `test_device_member_with_write_is_refused_like_a_session` and updated assertion to expect 403 refusal instead of 200. The test now also verifies that the same member's session upload is refused (404 or 403).