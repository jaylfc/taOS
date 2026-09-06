### Fixed
- Route PowerShell VMAF eval diagnostics (source/variant not found, ffmpeg failure) to stderr via `[Console]::Error.WriteLine` instead of `Write-Host`, so the ffmpeg-failure diagnostic no longer pollutes the stdout CSV stream on PowerShell 6+ and breaks the header/data-row contract
