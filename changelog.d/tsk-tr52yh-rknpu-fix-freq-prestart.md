### Fixed
- The install-rknpu.sh script now runs the rkllama fix_freq script as a privileged ExecStartPre in the systemd unit, ensuring NPU and DDR frequency pins are applied even when the service runs as a non-root user.
