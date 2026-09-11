### Fixed

- Fleet quarantine is now durable: `bounce_card()` records a strike through the board store alongside the existing `/tmp/taos-attempts-*` write, so `strike_count` reflects actual bounces even after `/tmp` is wiped.
- `_burned()` consults the durable `strike_count` first; a card with `strike_count >= 3` is burned regardless of whether the `/tmp/taos-attempts-*` file is present. The `/tmp` file is kept as a fallback when the store is unreachable.
