### Fixed

- The Keenable keyless-search switch no longer stays disabled after a transient save failure: a failed save shows the error but leaves the switch usable so it can be retried, a failed initial load still disables it and offers Retry, and overlapping Retry clicks now apply only the latest response so a slow earlier reply can no longer overwrite a newer one.
