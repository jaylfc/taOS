# RED-FIRST proof for tsk-k2x4ci (fix-forward #4259)

The new test `test_block_fails_closed_when_contacts_store_uninitialised` was
written first and run against the #4259 head (the `_db` guard still present in
`tinyagentos/routes/hub.py:696`). It FAILED, proving the fail-open the card
closes:

```
FAILED tests/test_collab_a2_handshake.py::TestBlockCascade::test_block_fails_closed_when_contacts_store_uninitialised - assert 200 == 500
```

A contacts store that exists but is not initialised SKIPS the whole cascade
(no `get_contacts_by_fingerprint`, no `revoke_peer_link`) and the route still
returns `{"state": "blocked"}` with 200 — the same fail-open moved to a new
trigger.

After step 1 (restoring the guard to `if contacts_store is not None:`), the
uninitialised store reaches the try block, raises, and returns the new 500
`{"state": "block-incomplete", "peer": peer}`:

```
1 passed in 3.15s
```