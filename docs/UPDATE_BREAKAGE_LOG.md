# Update breakage log

Check this file FIRST when a user reports something broken after an update. Each entry
records a change that can break existing installs, who is affected, the symptom, how to
check, and the fix. Newest first.

This file is maintained as part of the release/update audit: any merge that changes
behavior for EXISTING installs (ports, paths, auth, migrations, service names) must add
an entry here in the same PR. Agents helping users troubleshoot should fetch the latest
version from:

`https://raw.githubusercontent.com/jaylfc/tinyagentos/master/docs/UPDATE_BREAKAGE_LOG.md`

Format per entry: date, change (PR), affected installs, symptom, check, fix.

---

## 2026-06-12 — rkllama default port moved 8080 -> 7833 (#795)

- **Affected:** installs that re-run `install-rknpu.sh` after this change while
  their taOS config still points at 8080 (i.e. their recorded backend URL is
  `http://localhost:8080` or `http://<host>:8080`).
- **Symptom:** model pulls and chat fail with connection refused on 8080 after
  re-running the installer, because the new systemd unit listens on 7833.
- **Check:** `systemctl cat rkllama | grep port` -- should show `--port 7833`
  on new installs. Compare against the backend URL shown in Settings; if it
  still says `:8080` and rkllama is now on 7833, that is the mismatch.
- **Fix:** re-run `install-rknpu.sh` (updates the systemd unit to 7833) AND
  update the backend URL in Settings to `http://localhost:7833`, OR set
  `TAOS_RKLLAMA_PORT=8080` before re-running to keep the old port.
- **New installs:** unaffected -- the installer and all taOS defaults already
  use 7833.
- **Note:** the controller also probes port 8080 as a legacy fallback when
  nothing is listening on 7833, so read-only operations (model list, health
  checks) will still work automatically on mixed installs.

## 2026-06-11 — Docker app shortcuts recorded the container port (#788)

- **Affected:** docker-backed store apps installed BEFORE #788 on builds that already
  allocated pool ports.
- **Symptom:** the app's Launchpad shortcut opens the wrong port (usually the app's
  internal default like 8080) and the page does not load, while the app itself runs
  fine on its allocated port.
- **Check:** compare the shortcut's port with `docker ps` port mappings for the app
  container (host side of `host:container`).
- **Fix:** uninstall and reinstall the app from the Store (the reinstall records the
  allocated port), or edit the shortcut to the host port shown by `docker ps`.

## 2026-06-11 — NPU installer died on minimal images missing `strings` (#786)

- **Affected:** fresh installs on RK3588/RK3576 boards (Orange Pi 5, ROCK 5B) using OS
  images without binutils, installed before #786.
- **Symptom:** install completes without rkllama; pulling any NPU model fails with
  `rkllama /api/pull failed: All connection attempts failed` (nothing listening
  on :8080).
- **Check:** `systemctl status rkllama` (or whether anything answers on :8080).
- **Fix:** re-run the installer one-liner from the README (master includes #786); it
  now completes the rkllama setup.

## 2026-06-10 to 2026-06-11 — Cluster workers now require pairing (#737, #762, #770)

- **Affected:** cluster workers set up BEFORE pairing auth landed.
- **Symptom:** worker register/heartbeat rejected with 401 `worker_not_paired` or
  `bad_signature`; worker shows offline in the Cluster app and logs a re-pair prompt.
- **Check:** worker log shows the 401 code; Cluster app shows the worker pending or
  missing.
- **Fix:** re-pair once: restart the worker agent, it prints a pairing code; approve it
  in the Cluster app. Workers updated past #785 recover without a restart after
  approval.

## 2026-06-10 — Knowledge store user_id migration (#763)

- **Affected:** installs created before multi-user keying that updated across the
  change.
- **Symptom:** controller failed to start (pre-#763) or knowledge/memory lookups came
  back empty after update.
- **Check:** controller journal for the knowledge migration log lines on boot.
- **Fix:** update to a build including #763; the migration self-heals on startup. If a
  start-failure loop persists, see docs/runbooks (controller rescue).

## 2026-06 — App installs moved to allocated high ports (#695 era)

- **Affected:** store apps installed before port allocation landed; they may sit on
  core ports (8080, 80, 3000, ...) that the platform or other services need. Legacy
  installs do NOT self-migrate (tracked in #695).
- **Symptom:** a platform service cannot bind its port (e.g. rkllama on :8080 never
  comes up) or two apps collide; the squatting app works, the rightful service does
  not.
- **Check:** `docker ps` port mappings against the platform's reserved ports
  (6969, 6970, 4000, 7832, 7900, 8080, 80, 443).
- **Fix:** uninstall the squatting app and reinstall from the Store; it lands on a
  30000-40000 pool port and the shortcut follows it (with #788).

## 2026-06 — Controller install layout standardized (non-root /opt)

- **Affected:** early installs cloned to other paths/users (e.g. ~/tinyagentos or
  root-owned checkouts).
- **Symptom:** update pulls fail ("dubious ownership", permission errors), services
  point at stale paths.
- **Check:** `systemctl cat taos` ExecStart/WorkingDirectory vs where the repo
  actually lives; `git -C <dir> status` for ownership errors.
- **Fix:** re-run the installer (it repairs ownership per #768), or follow
  docs/runbooks controller rescue.
