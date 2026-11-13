### Fixed

- busctl() pipe drain after kill now completes even if the caller cancels a second time during the drain, preventing pipe transport leaks.