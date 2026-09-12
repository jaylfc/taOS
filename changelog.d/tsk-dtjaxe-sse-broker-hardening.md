### Fixed
- Projects SSE broker now assigns a unique `id` to each event, emits it in the SSE stream as `id:`, and honours `Last-Event-ID` on reconnect so stale events are not replayed.
- Subscriber queue is bounded to 256 events with drop-oldest backpressure, preventing unbounded memory growth on slow consumers.
- The board live badge now reflects actual connection state via `onopen`/`onerror` callbacks instead of being hard-coded to connected.
- Client-side SSE deduplication by event `id` prevents re-processing of replayed events.
