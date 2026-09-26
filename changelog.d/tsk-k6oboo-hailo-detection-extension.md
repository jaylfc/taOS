### Fixed

Extended the pre-existing `hailo-ollama` detection in `scripts/install-hailo.sh` to catch installed-but-stopped upstream instances:

- Detect upstream `hailo-ollama.service` units that exist but lack the `OLLAMA_HOST=127.0.0.1:7836` marker
- Detect upstream `hailo-ollama` binaries on PATH that resolve outside the install directory
- All detection tests pass, including the critical "our own install" negative test to prevent false positives
- The refusal message now specifies which signal fired (upstream unit vs upstream binary)
- Preserved the existing live probe on port 8000 as the cheap and unambiguous case
