### Fixed

- Folded CodeRabbit findings on #2763: consolidated duplicate deadline checks in the port-open and ready wait loops, floored `curl --max-time` at 1 s so a future guard edit cannot disable the per-attempt timeout, anchored the test assertions to each phase, and corrected the `tsk-wgsns5` changelog wording (per-probe timeout and cold-boot timing)
