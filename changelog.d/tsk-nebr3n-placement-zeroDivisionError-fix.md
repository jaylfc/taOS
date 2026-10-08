### Fixed

- Fixed ZeroDivisionError in `score()` when hash is near 2**64 by using top 52 bits instead of 64: `u = ((h >> 12) + 0.5) / 2**52`