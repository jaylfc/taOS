### Fixed

- Added polyfill for `AbortSignal.timeout` to support Chrome < 103. This ensures the backend health poll works on older browsers that don't have the native implementation. The polyfill creates an AbortSignal that times out after the specified duration.