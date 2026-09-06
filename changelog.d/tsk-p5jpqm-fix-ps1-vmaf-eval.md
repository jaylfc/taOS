### Fixed
- Fixed PowerShell VMAF eval harness to emit `ERROR` for both `vmaf_mean` and `saving_pct` in the no-score branch, matching the bash harness; injected controlled fake ffmpeg into all three PS1 tests via PATH to make them deterministic
