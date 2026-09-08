### Fixed

- OTel span IDs are now derived deterministically from the envelope id using SHA-256, so child spans whose parentSpanId is derived from parent_id can correctly reference their parents. Traces emitted by the OTLP emitter now nest properly in Jaeger, Tempo, and Grafana.
