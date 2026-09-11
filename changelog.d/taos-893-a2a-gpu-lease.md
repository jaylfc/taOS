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
