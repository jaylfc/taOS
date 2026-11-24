### Fixed

- **Agent import (Hermes): gateway verification now checks systemd environment** — after restarting the Hermes gateway, the import now reads the unit's `Environment` property via `systemctl show` and waits for the unit to reach `active` state, proving the gateway serves the imported profile with the correct `HERMES_HOME`.

- **Agent import (Hermes): bundle members are now stripped, not just listed** — the import rewrites the uploaded tar.gz to a new archive containing only regular files and directories; symlinks, hardlinks, devices, FIFOs, and absolute/traversal paths are dropped and reported. Zip bundles are rejected with a clear message (Hermes only accepts tar.gz).

- **Agent import (Hermes): model/provider rewrite preserves full config** — the profile's `config.yaml` is parsed as YAML and only the `model.*` keys are replaced with taOS gateway values; all other sections are kept. The original is atomically backed up to `config.yaml.pre-import`; a failed backup now fails the import.

- **Agent import (Hermes): pause_jobs field verified against Hermes source** — jobs.json path and field names (`enabled`, `state`) match the Hermes version pinned in `scripts/install_hermes.sh` (HERMES_TAG=v2026.8.31).

- **Agent import (Hermes): transfer notice delivered to memory and chat** — the short form is appended to the profile's `memories/MEMORY.md`; the full notice is staged as the first chat message via the same internal bridge endpoint a deploy uses (`/internal/first-message`), with a file fallback at `/root/.taos/first_message.txt`. The notice includes the source host name (or "another machine").

- **Agent import (Hermes): failed imports leave no stale agent record** — on deploy failure the agent entry is removed; on post-provision failure the entry is marked `failed` with a `failure_reason` and no dangling name reservation.

- **Agent import (Hermes): name reservation happens before bundle validation** — the slug is reserved up front and released on validation failure, fixing a regression where a fake test bundle caused a 400 before reservation release.

- **Agent import (Hermes): security fix for shell heredoc interpolation** — all container writes now use `push_file` with temporary files instead of `bash -c` heredocs, eliminating injection of untrusted bundle content (member names, base_url lines) into container shell commands.

- **Agent import (Hermes): timezone-aware datetime and trailing newlines** — `datetime.utcnow()` replaced with `datetime.now(UTC)`; all generated files end with a newline.
