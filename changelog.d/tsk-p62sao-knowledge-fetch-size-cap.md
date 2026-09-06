### Fixed

- Knowledge fetches (article ingest, monitor re-fetch, Library web processor) now
  stream responses through a shared `stream_text_response` helper that rejects
  non-text content-types and caps the buffered body at 10 MB, preventing a
  malicious or misconfigured URL from exhausting host memory with a multi-GB
  response.
