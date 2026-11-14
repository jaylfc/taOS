### Fixed

* Fix race condition in local token validation by adding `AuthManager.resolve_local_token()` that reads host token and bindings atomically
* Store agent canonical ID alongside name in local token bindings for proper fence comparison
* Update middleware to use single atomic read instead of separate validate_local_token/get_local_token_agent calls