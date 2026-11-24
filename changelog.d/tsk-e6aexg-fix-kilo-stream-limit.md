### Fixed
- Kilo harness run_turn now reads subprocess stdout lines longer than 64 KiB without raising ValueError; subprocess stdout line reader limit increased to 16 MiB.