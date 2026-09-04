### Fixed

- Capped installer wait probes by the remaining phase time (`curl --max-time "$_curl_timeout"`), so no probe can run past its phase deadline: the first probe may get the full budget (60 s port-open, 240 s ready) and every later probe only the time still left in the phase
- Increased `_PORT_WAIT` from 30 to 60 seconds, matching the documented 55–65 s cold-boot bind time on Pi 5 / Orange Pi 5 (no margin added; the new value sits inside that range)
- Added elapsed-time deadline tracking to both the port-open and ready wait loops, ensuring configured phase limits are enforced as wall-clock time rather than just attempt counts
