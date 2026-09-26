### Security

- Lock-screen control POSTs (`/auth/lock-*`) now refuse "simple" requests: the caller must send `X-taOS-Console` or `Content-Type: application/json`, so a web page in a local browser can no longer drive power, radios, torch, volume or the power menu with a no-cors fetch.
- "Stop all agents" from the lock screen now requires a signed-in session (401 otherwise); power off and restart stay pre-auth, as the hardware key already allows both.
- The lock screen page sends the console header on every control POST. The handset scripts taos-kiosk-power-hold and taos-kiosk-screen must add `-H "X-taOS-Console: 1"` (taos-kiosk-volume already sends JSON).
