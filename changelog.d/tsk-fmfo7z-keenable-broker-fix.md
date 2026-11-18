### Fixed

- Keenable broker routes now authenticate/authorize before validating request body, ensuring unauthenticated callers get 403 instead of 400 that reveals schema details
- Added success-path tests for Keenable broker routes covering various authentication and authorization scenarios
- Fixed trailing whitespace in validator functions
