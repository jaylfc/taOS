### Fixed
- Download cancellation now properly cleans up temp files when the task is cancelled mid-stream (asyncio.CancelledError during chunk iteration).
- Corrected stale comment for `_DOWNLOAD_IDLE_TIMEOUT`: it is a per-read idle timeout, not a total transfer cap.
