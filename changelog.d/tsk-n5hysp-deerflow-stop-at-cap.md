### Fixed
- The deer-flow bridge stops waiting for a free slot when shutdown begins, so it exits inside its systemd stop timeout instead of blocking until a pending handler finishes.
