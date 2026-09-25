### Fixed

- Remove inert auto_update and update_channel keys from PicoClaw installer config writing block that overwrote user config files. The installer now only sets the binary read-only permission as v0.3.1 ships no self-update command.