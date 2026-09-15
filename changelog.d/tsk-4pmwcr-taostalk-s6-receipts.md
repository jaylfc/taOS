### Added
- A2A per-recipient read receipts in taOStalk: own messages now show a three-state tick (sent/delivered/seen) derived from GET /api/a2a/messages/{id}/receipts, with missing receipts rendering as sent rather than unseen. Opening a thread marks incoming messages as seen via PATCH /api/a2a/receipts, and the bus stream is subscribed for live receipt updates.
