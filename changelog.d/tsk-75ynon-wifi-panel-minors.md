### Fixed

- WifiPanel now accepts whitespace-only passphrases (8 to 63 characters). The Enter guard and Join button compare the password against the empty string without `trim()`, so a passphrase of eight spaces enables Join and submits it.
- The Wi-Fi toggle and disconnect handlers no longer wait unbounded on the refresh GET after a successful POST. The refresh is raced against a 10 s timeout so the busy flag always clears; a later poll still refreshes the state.