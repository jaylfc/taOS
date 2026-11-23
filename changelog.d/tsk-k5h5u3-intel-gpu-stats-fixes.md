### Fixed

- Intel GPU name resolution now parses real pci.ids format (unquoted, tab-indented vendor/device lines, ignores subsystem lines and comments)
- Intel GPU stats tests are now host-independent (fake all sysfs paths including device/vendor and device/device)
- Activity UI no longer crashes on first sample: LoadBar handles null load_percent gracefully
- Intel GPU frequency sysfs paths corrected per kernel documentation (gt_act_freq_mhz, gt_cur_freq_mhz, gt_max_freq_mhz directly under cardN/, not under gt/gt0/)
- Intel GPU load calculation uses time.monotonic() to avoid NTP adjustments; residency counter resets yield null not 100%
- GPUs deduplicated and joined to runtime stats by PCI address, not by model name or list position
