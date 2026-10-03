### Fixed

- Anthropic backend: normalise the route base URL so the default `https://api.anthropic.com/v1` no longer 404s as `/v1/v1/messages`.
- Anthropic backend: route streaming through `_stream_with_retry` with per-route URL and key, and record usage from the `message_delta` usage event.
- Anthropic backend: wrap `httpx.TimeoutException` and `httpx.HTTPError` in `upstream_error` so they fail over like other providers.
- Anthropic backend: omit the usage block from the response when the upstream did not report usage, and mark the trace as `usage_estimated`.
- Anthropic backend: record spend and trace inside `_call_one(route)` so failover credits the live backend, not the dead one.
- Anthropic backend: filter the failover walk to routes with `provider == "anthropic"` so a same-named non-Anthropic route never receives an Anthropic-formatted request.
- Anthropic backend: stream usage is tracked via `AnthropicStreamUsage`; streams without a `message_delta` usage event are recorded as unknown usage with `usage_estimated=True` and a conservative budget estimate.
- Anthropic backend: the streaming loop exits both the frame and stream iterators after `message_stop` so trailing events are not yielded after the `[DONE]` terminator.
- Anthropic backend: `_StreamTranslator.feed` accumulates `completion_text` from `content_block_delta.text_delta` events so the streaming trace captures the response body.
- Anthropic backend: `_call_anthropic` and `_stream_one` share a single `_messages_url(route)` helper so the normalisation cannot drift.
