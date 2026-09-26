### Security

- LLM gateway failover now sends each backend only its own API key, resolved per attempt; a failover no longer carries the first backend's key to a later backend (for example a third-party host or a plain-http LAN server). Backends the gateway cannot speak to are dropped from the failover list.
- LLM gateway streaming now maps an upstream non-2xx answer the same way as the non-streaming path: a 5xx or a rejected key fails over, a caller 4xx returns an OpenAI-shaped error with the key redacted, and the raw upstream error body is never relayed.
