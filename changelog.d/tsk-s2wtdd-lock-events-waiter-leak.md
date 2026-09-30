### Fixed
- `/auth/lock-events` no longer leaks a listener queue when the client drops the response before the stream body is iterated. Previously the queue was registered in the handler body, so an aborted client left it in `_LOCK_EVENT_WAITERS` forever.
