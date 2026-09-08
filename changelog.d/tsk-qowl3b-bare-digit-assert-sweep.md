### Fixed

- `tests/test_auth_pin.py::TestPinStore::test_pin_is_hashed_not_stored_in_clear` now parses the JSON store and asserts on the `pin_hash` field directly (`$argon2` prefix, not equal to the PIN value) instead of checking that the bare PIN string does not appear anywhere in the raw file. The old assertion could flake when a runtime-generated argon2 salt or hash happened to contain the PIN digits as a base64 substring. Added a deterministic regression test that monkeypatches the hasher to embed the PIN in the encoded hash and proves the new assertion holds while the old one fails.
