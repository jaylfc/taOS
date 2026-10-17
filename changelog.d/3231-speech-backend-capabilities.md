### Added
- Speech backends are now routable as capabilities: `BACKEND_CAPABILITIES`
  gains `asr` labels for `faster-whisper` / `whisper-cpp` and `tts` labels
  for `kokoro` / `sglang` / `vllm-omni`, and `SOFTWARE_TO_BACKEND_TYPE` maps
  `whisper`, `kokoro` and `moss` so a worker running a whisper or TTS server
  is selected by `TaskRouter.route_request("asr")` / `("tts")`.
