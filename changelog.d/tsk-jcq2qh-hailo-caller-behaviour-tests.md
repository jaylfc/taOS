### Changed
- Replaced source-text caller tests for install-hailo.sh with behavioural tests that run the real `install_hailo_if_pending` and `chain_hailo_installer` functions against stubbed exit codes 3, 1, and 0, ensuring the conflict message on 7836 and the generic fallback are verified by execution, not regex.
