### Fixed

- Fixed busctl() not properly cleaning up the child process when the caller is cancelled. The process now gets killed and reaped, then the CancelledError is re-raised to propagate the cancellation.