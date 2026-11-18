### Fixed

- Hub member-access tests now assert on the real relationship kinds (`friend_request_out`, `friend_request_declined`, ...) via the `REL_*` constants instead of nonexistent string kinds, so the 403 store-unchanged assertions can actually fail. Shell-profile `env -S` deny test now asserts the refusal message identifies the denied command (`ls`).
