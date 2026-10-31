### Fixed
- Preserve existing secret grants when the native taOS Agent stores the harness token via the sign-in broker (previously `claude_code_oauth_token` grants to other agents were lost).
- Cap open sign-in requests to 3 per agent; the 4th returns `429 Too Many Requests` with detail "too many open sign-in requests".
- Clear expired `code_ready` rows' code column on new request creation (previously codes persisted indefinitely after expiry).