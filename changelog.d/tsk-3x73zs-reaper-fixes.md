### Fixed
- Skip executor.sh processes with unknown `create_time` instead of treating them as epoch-old.
- Only report a process as reaped after successful termination; catch `psutil.AccessDenied` on kill so the scan continues.
