### Fixed
- Fixed duplicate contradictory receipt ticks on dm-remote channels by removing the legacy delivered_at branch and adding a dm-remote exclusion guard to the new ReceiptTick path
- Fixed EventSource onerror handler that permanently killed live receipt streams by removing the close() call (keeping only the cleanup close on unmount)
- Fixed live thread replies not being marked as seen when thread opens by including liveReplies in the mark-seen effect and dependency list