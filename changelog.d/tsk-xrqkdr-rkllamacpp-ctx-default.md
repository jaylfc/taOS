### Changed
- rk-llama.cpp now starts with a 12288-token context by default, the measured safe NPU ceiling on RK3588; set TAOS_RKLLAMACPP_CTX to change it