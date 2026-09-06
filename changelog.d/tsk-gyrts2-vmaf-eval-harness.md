### Added
- Library P4 research spike eval harness: `scripts/library-vmaf-eval.sh` and `scripts/library-vmaf-eval.ps1` compute VMAF per (source, variant) pair via ffmpeg libvmaf and emit CSV (video, variant, vmaf_mean, bytes_source, bytes_variant, saving_pct). Four 1-second 320x240 fixture clips under `tests/fixtures/` are included for local and CI runs (#tsk-gyrts2).
