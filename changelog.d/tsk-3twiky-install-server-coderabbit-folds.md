### Fixed

- Folded CodeRabbit findings on #2763: collapsed the duplicated deadline checks in the port-open and ready wait loops down to one guard before each probe and one after, floored `curl --max-time` at 1 s so a future guard edit cannot disable the per-attempt timeout, anchored the test assertions to each phase, and corrected the `tsk-wgsns5` changelog wording (per-probe timeout and cold-boot timing)
- Anchored each installer wait phase to an absolute deadline (`_port_deadline` / `_ready_deadline`) and re-read the clock after every probe, so a slow `curl` can no longer let the follow-up `sleep` carry the port-open or ready phase about a second past `_PORT_WAIT` / `_READY_WAIT`
