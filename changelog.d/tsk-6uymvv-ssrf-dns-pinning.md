- Security: the SSRF guard now pins each outbound connection to the address it
  validated. Fetches of user-supplied URLs (browser proxy, extract, download,
  Library web ingest, Knowledge article ingest, peer handshake delivery,
  UnifiedPush) go through a guarded client whose connections resolve and check
  the hostname once and connect to that answer, so a low-TTL nameserver can no
  longer answer public to the check and 127.0.0.1 to the connection. TLS
  verification is unchanged and still validates the original hostname.
