### Fixed

- De-duplicate `final` reply POSTs by message id so a bridge retry or SSE replay cannot create a second chat message for an agent.
