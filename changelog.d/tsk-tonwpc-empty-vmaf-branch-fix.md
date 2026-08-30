### Fixed
- Fixed empty-VMAF branch in both bash and PowerShell harnesses: when ffmpeg exits zero without a VMAF score, emit `ERROR` for both `vmaf_mean` and `saving_pct` columns and exit non-zero. The PowerShell output now matches the bash output exactly.
- Fixed temp-file leak in the bash harness: `TMPFLAG` was only removed on the success path, leaving the file behind on every failing run.
