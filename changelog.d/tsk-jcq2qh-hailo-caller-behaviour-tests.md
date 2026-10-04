### Changed
- Replaced source-text caller tests for install-hailo.sh with behavioural tests that run the real `install_hailo_if_pending` and `chain_hailo_installer` functions against stubbed exit codes 3, 1, and 0, ensuring the conflict message on 7836 and the generic fallback are verified by execution, not regex.
- Restored 8 accidentally deleted `detect_preexisting_hailoollama` behavioural tests and consolidated caller tests into a single parametrized test over caller x exit code.
- Moved `chain_hailo_installer()` above the accelerator-detection comment block in `install-worker.sh` and fixed its formatting to match surrounding style.
