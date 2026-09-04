### Fixed

- taOS Pocket (`creations/taos-pocket/index.html`) made the Notifications and Decision cards reachable via bottombar tabs (and Left/Right keys), forwarded request `options` through `api()` so the decision accept call goes out as a proper `POST` with a JSON body, and only clears `state.decision` after the accept call resolves successfully so the user can retry on failure.