### Fixed

- The in-app update now refreshes the installed graceful-stop script and systemd unit (system and user) so installs that already have the old drain-on-restart unit and stop script no longer drain agents on every controller or update restart; when the paths are not writable the update result reports it instead of staying silent.
