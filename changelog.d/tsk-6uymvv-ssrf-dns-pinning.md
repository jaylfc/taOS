- Security: the SSRF guard now pins each outbound connection to the address it
  validated. Fetches of user-supplied URLs (browser proxy, extract, download,
  Library web ingest, Knowledge article ingest, peer handshake delivery,
  UnifiedPush) go through a guarded client whose connections resolve and check
  the hostname once and connect to that answer, so a low-TTL nameserver can no
  longer answer public to the check and 127.0.0.1 to the connection. TLS
  verification is unchanged and still validates the original hostname.
- Fix: Library web ingest now reuses a single guarded client across an entire
  redirect chain instead of building and tearing down a fresh one (new
  connection pool, SSL context, pinned backend) on every hop.
- Fix: Knowledge article ingest now rejects a caller-supplied `fetch_client`
  that is an `httpx.AsyncClient` but was not built by `guarded_async_client`,
  instead of silently accepting an unguarded client and bypassing the SSRF
  pin.
