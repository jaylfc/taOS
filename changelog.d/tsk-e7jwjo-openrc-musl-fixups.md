### Fixed
- Added OpenRC service support for musl-arm64 (Alpine, postmarketOS) llama.cpp installs: when systemd is absent and OpenRC is present, writes /etc/init.d/llama-cpp as an openrc-run script and enables it via rc-update
- Added trap cleanup in build_from_source_musl to remove the llama.cpp source directory on EXIT/ERR
- Added test isolation: unset TAOS_LLAMACPP_VARIANT at top of test file, added OpenRC test case with mocked service manager detection
- Updated README.md with one-line note that musl-arm64 builds from source and OpenRC is supported
