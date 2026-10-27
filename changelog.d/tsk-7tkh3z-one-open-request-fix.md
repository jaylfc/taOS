### Fixed

- Made the one-open-request-per-kind check atomic inside AuthBrokerStore.create to prevent race conditions
- Added OpenRequestExists exception for better error handling
- Removed duplicate open request check in routes/auth_broker.py
- Updated store tests to properly move requests to code_read before testing caps
- Fixed documentation to correctly describe pending or code-ready requests