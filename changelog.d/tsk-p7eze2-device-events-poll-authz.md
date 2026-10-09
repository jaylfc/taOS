### Fixed
- Device events stream now re-checks authorization before yielding each event frame in the poll branch, preventing data leakage after device revocation.