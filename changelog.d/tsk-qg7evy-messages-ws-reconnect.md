### Fixed

- MessagesApp no longer leaks a zombie WebSocket after unmount; reconnect timers are cancelled on cleanup, delays are jittered to prevent lockstep reconnects, and reconnection stops after 20 failed attempts.
