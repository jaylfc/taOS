### Fixed
- An agent holding a global `decisions_write` grant plus grants on specific projects now sees its decisions in those projects too on `GET /api/decisions/agent` and `GET /api/decisions`, not only its null-project ones.
