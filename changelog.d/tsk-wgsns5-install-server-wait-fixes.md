### Fixed

- Capped installer wait probes by the remaining phase time (`curl --max-time "$_curl_timeout"`), so a probe can run for up to 60 s during port-open and 240 s during ready instead of the full phase budget
- Increased `_PORT_WAIT` from 30 to 60 seconds, matching the documented 55–65 s cold-boot bind time on Pi 5 / Orange Pi 5 (no margin added; the new value sits inside that range)
- Added elapsed-time deadline tracking to both the port-open and ready wait loops, ensuring configured phase limits are enforced as wall-clock time rather than just attempt counts
