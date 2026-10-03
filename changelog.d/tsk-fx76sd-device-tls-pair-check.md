### Fixed

- Device TLS: reuse the stored cert only when its key matches. Also write key before cert to prevent interrupted writes from leaving a cert without a matching key.
