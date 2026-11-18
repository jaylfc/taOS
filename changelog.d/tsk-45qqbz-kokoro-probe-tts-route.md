### Fixed

- Worker: the default kokoro probe candidate (:8880) is only advertised as a kokoro tts backend when GET /v1/audio/voices returns HTTP 200 with a 'voices' list, so any other server on :8880 answering /v1/models is no longer mistaken for kokoro. Configured kokoro backends (manifest or TAOS_EXTRA_BACKENDS) keep the /v1/models probe.
- Cluster: TaskRouter.route_request refuses capability 'tts' up front with HTTP 503 ("tts returns binary audio; call the worker directly") instead of routing to a kokoro worker whose binary audio would make resp.json() raise and trip the circuit breaker.
- Worker: _probe_kokoro no longer lets a /v1/models error escape detect_backends; any exception from the models call is caught and returns None so kokoro is treated as down.
- Worker: a kokoro configured via TAOS_EXTRA_BACKENDS or the manifest at the default :8880 URL now correctly receives the generic /v1/models probe instead of the default candidate's voices check.
