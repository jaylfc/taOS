### Added

- Peer handshake completion: friend-accept now delivers a signed handshake to the peer's recorded endpoints and a new `POST /api/peer/handshake` route (no bearer, authenticated by the envelope signature plus a recorded friend-request edge) pins the keys, stores the exchanged tokens and answers with a signed reply, so both nodes end up with working inbound and outbound peer tokens.

### Fixed

- Peer delivery (handshake and collab invites) goes through the SSRF guard with the contact's recorded `(host, port)` endpoints as the only private-range allowance; loopback, link-local and unrecorded CGNAT addresses stay blocked, and the collab-invite delivery loop no longer uses an unguarded HTTP client.
