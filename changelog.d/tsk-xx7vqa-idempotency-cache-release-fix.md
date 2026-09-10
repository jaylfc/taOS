### Fixed

- **IdempotencyCache:** `release()` now removes the key when the handler raised instead of leaving `None` cached, so a retry with the same `Idempotency-Key` executes the handler again instead of receiving 503 for the TTL duration. `set()` now stores `(status_code, body)` tuples so cached error responses replay with their original status code instead of being returned as 200.
