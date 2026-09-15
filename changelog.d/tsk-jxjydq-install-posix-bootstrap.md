### Fixed

- The controller install one-liner now works on images that ship no bash (Alpine, postmarketOS). The script gained a POSIX `sh` bootstrap that installs bash and re-execs itself, the documented command pipes into `sudo sh` instead of `sudo bash`, and the Alpine package list installs bash. The README also gained per-distro collapsible dependency fallbacks.
