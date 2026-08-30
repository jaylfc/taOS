### Fixed
- Fixed empty-VMAF branch in bash harness: moved `saving_pct` computation before the `vmaf_mean` null check, emit `ERROR` for both `vmaf_mean` and `saving_pct` in the error branch
