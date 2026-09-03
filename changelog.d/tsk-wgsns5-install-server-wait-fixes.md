### Fixed

- Added `--max-time 5` per-attempt timeout to both health and cluster/workers curl probes, preventing a single stuck iteration from stalling the installer indefinitely
- Increased `_PORT_WAIT` from 30 to 60 seconds, providing a safety margin above the documented 55–65 s cold-boot bind time on Pi 5 / Orange Pi 5
- Added elapsed-time deadline tracking to both the port-open and ready wait loops, ensuring configured phase limits are enforced as wall-clock time rather than just attempt counts