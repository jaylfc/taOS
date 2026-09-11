### Added

- Shared-GPU coordination over the A2A bus (taOS #893). Agents sharing one card
  can `GET /api/a2a/gpu/check` (folds the channel's open `[GPU CLAIM]`/
  `[GPU RELEASE]` messages into the claims still held and subtracts them from
  the node's live free VRAM), then `POST /api/a2a/gpu/claim` for an
  admission-checked claim that both registers a real cluster lease (TTL, kept
  alive with `POST /api/a2a/gpu/renew`) and posts the `[GPU CLAIM]` line peers
  read; `POST /api/a2a/gpu/release` frees it and `POST /api/a2a/gpu/request`
  asks for a window when blocked. The cluster lease applies to nodes the
  controller knows as cluster workers; a node it does not know is coordinated
  over the bus alone (admission-checked and posted, with no local TTL).
  Claiming refuses (409) on another holder's
  open claim or insufficient VRAM, and CHECK/CLAIM return 503 rather than
  reporting a node free when the bus cannot be read, so two agents can no
  longer silently co-load past the card's VRAM. An agent posts as its own
  registry identity (scope `a2a_receive` to check, `a2a_send` to act) and the
  node label resolves to a cluster worker's heartbeat VRAM or the controller's
  own shared VRAM ledger.
- Claims carry their own expiry. `POST /api/a2a/gpu/claim` publishes
  `expires=<unix ts>` on the `[GPU CLAIM]` line (the backing cluster lease's
  expiry, or the requested TTL for a bus-only node) and `POST /api/a2a/gpu/renew`
  reposts that line as it extends the lease, so a holder that keeps its lease
  alive keeps its claim alive. The fold drops a claim whose published expiry has
  passed, so a holder that crashed or stopped keeping alive no longer blocks the
  shared card until its claim ages out of the fold window. A claim posted by hand
  without an `expires=` is unchanged: bounded by a RELEASE alone.

### Fixed

- Freeing another holder's GPU lease by explicit `lease_id` (the operator
  override, `POST /api/a2a/gpu/release`) posts the `[GPU RELEASE]` line as the
  **freed holder** rather than as the operator. A claim is keyed on its bus
  author, so the old line cleared nothing: the local lease was gone while every
  peer's fold still read the node as claimed, blocking a GPU that was actually
  free. The response now distinguishes `holder` (who acted) from
  `released_holder` (whose claim the line closes).
