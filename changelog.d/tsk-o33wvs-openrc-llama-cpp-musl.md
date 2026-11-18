### Fixed
- OpenRC llama-cpp service on musl-arm64: export LD_LIBRARY_PATH in start_pre so the deleted build tree's shared libs are found and the /health gate passes, quote the models path in command_args, require openrc-run, rc-service and rc-update before dispatching, and suggest `rc-service llama-cpp status` in the health-timeout message
