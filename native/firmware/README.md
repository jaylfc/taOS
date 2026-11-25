<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# taos-firmware

Firmware library and guarded flasher for LoRa mesh boards. It is an engine (no UI) with a CLI,
`taos-firmware`, and a local JSON API that the Survival Studio app calls. It moves a supported
board between Meshtastic, MeshCore and RNode (Reticulum) firmware, and keeps those firmwares on
disk so a board can be flashed with no internet.

Nothing is bundled with taOS. Firmware is fetched from each project's own GitHub releases the first
time the owner accepts that project's licence, and refreshed in the background.

    taos-firmware [--json] boards | library | refresh | licence | devices | compatible
                           | flash plan|run | consent | backups | log | serve

`--json` prints exactly one JSON object on stdout. Exit code 0 on success, 1 on failure. Without
`--json`, a few commands print a short table and the rest print indented JSON.

## Boards

| id | name (typed to confirm) | chip | Meshtastic | MeshCore companion | RNode |
|---|---|---|---|---|---|
| heltec-v3 | Heltec V3 | esp32s3 | yes | yes | yes |
| heltec-v4 | Heltec V4 | esp32s3 | yes | yes | yes |
| tbeam | LilyGO T-Beam | esp32 | yes | yes (SX1262) | yes (SX1262) |
| tbeam-s3 | LilyGO T-Beam Supreme | esp32s3 | yes | yes | yes |
| rak4631 | RAK4631 | nrf52840 | yes | yes | yes |
| t1000e | Seeed T1000-E | nrf52840 | yes | yes | no RNode build |
| xiao-nrf52840 | Seeed XIAO nRF52840 | nrf52840 | yes | yes | no RNode build |

## Data directory

`$TAOS_FIRMWARE_DIR`, else `$XDG_DATA_HOME/taos/firmware`, else `~/.local/share/taos/firmware` (mode 0700).

    manifest.json        every stored firmware (see below)
    library/<network>/<version>/...   the files
    licences.json        who accepted which licence, for which first version, when (once per network)
    consent.json         time-limited grants that let a named agent flash
    flash-log.jsonl      one line per flash attempt, refusal and result
    backups/<id>/        configuration saved before a flash (0700, files 0600), with backup.json
    refresh-state.json, http-cache/   ETags and listings, so a refresh costs no API calls when nothing changed
    api-token            bearer token for the API (0600)

A manifest entry has: `id` (`network:board:version[:variant]`), `board`, `network`, `version`,
`variant` (`ble` or `usb` for MeshCore), `method` (`esptool`, `uf2`, `nrfutil`), `chip`, `files`
(role, path, sha256, size, flash offset), `licence_id`, `licence_url`, `source` (download URL),
`source_sha256`, `fetched_at`, `verification` and `unsigned_by_publisher`.

## Library and verification

`refresh` lists releases for each network, keeps the **latest stable and the previous stable**
(pre-releases and drafts are ignored; for MeshCore only `companion-v*` releases). Note that "previous stable" is
whatever GitHub marks as not a pre-release: for Meshtastic that was v2.7.15 when this was written, because v2.7.16
to v2.7.25 are flagged pre-release, downloads what
is missing, verifies it and prunes older versions once the newest is safely stored.

* Meshtastic: the per-chip release zip is checked against GitHub's sha256 digest for the asset,
  then only the supported boards' files are extracted (factory image, OTA image, file system image
  at the offsets in the release's own `.mt.json`; `.uf2` and `-ota.zip` for nRF52). The zip is deleted.
  Label `release-host-digest`, `unsigned_by_publisher: true` (the project publishes no signatures).
* MeshCore: the `-merged.bin` (ESP32, written at 0x0) or `.uf2` (nRF52) assets, same digest check, same label.
* RNode: also checked against the publisher's own `release.json` hash list. Label `publisher-checksum`.
* A file that does not match is deleted and reported; nothing about it enters the manifest.
* A release that cannot be used is not retried on every tick: a failed download or hash mismatch waits 6 hours, and a
  Meshtastic release that predates `.mt.json` flash maps (so its ESP32 offsets are unknown; v2.7.15 is one) is left
  out for ESP32 boards for good with a `notes` entry in the report. `refresh --force` (`{"force":true}`) clears this memory.
* Before every flash all files of the entry are hashed again against the manifest.

No licence is accepted for the person: `refresh` reports `licence_required` and downloads nothing
until `licence accept <network> --by <name>` (CLI) or `POST /v1/licences/accept`. The acceptance is
recorded once per network (who, licence id and URL, first version, when); a second call keeps the first record.

GitHub: no token. One listing call per network per run, `If-None-Match` ETags (a 304 is free), the
listing cached on disk, and on HTTP 403/429 the reset time is stored and the API is not called again
before it. Offline, rate limited and licence-pending are reported with exit code 0 when
`--quiet-offline` is given (that is what the timer uses). The MeshCore listing is large (hundreds of
assets per release); a refresh that finds nothing new costs one conditional request per network.

Environment overrides (used by the tests, and for a mirror): `TAOS_FW_API_BASE` (default
`https://api.github.com`; `http` is accepted only for 127.0.0.1). Downloads are only taken from GitHub hosts.

### Background job

`systemd/taos-firmware-refresh.service` and `.timer` (user units): daily, 10 minutes after boot,
`Persistent=true`, randomised delay, `Nice=15`, `Restart=on-failure` for real failures. The files are
shipped, not installed or enabled by this crate; the installer places them in `~/.config/systemd/user/`
and points `ExecStart` at the real binary path. `systemd/taos-firmware.service` runs the API.

## Detecting boards

`devices` lists boards found by USB id (read from `/sys/bus/usb/devices`, the tty found below it) and
UF2 bootloader drives (a mount containing `INFO_UF2.TXT`; the drive must already be mounted).
Each device has `id` (`usb-VID-PID-SERIAL-HUBPORT`; the hub port keeps two identical bridges apart, but the id changes if the
board is moved to another port), `kind` (`serial` or `uf2`), `port` or `mount`, `vid`/`pid`, `candidates`
(board ids it could be), `chip`, and `probe`.

`devices --probe` (API `?probe=1`) asks ESP32 boards for their chip with `esptool chip_id`, which
**resets the board**, so it is never done on a plain listing, and a flash probes only the device that was chosen, never the others.
The probe and the flash share one lock: while a flash or refresh runs, `/v1/flash/plan` answers 409 `busy` and the `?probe=1`
routes quietly skip the probe (`probe_skipped_busy:true`). The probe narrows the candidates by chip. A flash
always probes first and refuses if the probe did not confirm the chip (`probe_required`).
Some boards cannot be told apart (Heltec V4 and T-Beam Supreme share a USB id and chip; T-Beam
SX1262 vs SX1276): only the typed board name separates them.

Overrides for tests: `TAOS_FW_USB_ROOT`, `TAOS_FW_DEV_ROOT`, `TAOS_FW_UF2_ROOTS` (path list).

## Flash flow

1. **plan** (`flash plan`, `POST /v1/flash/plan`): runs every check that does not touch the board and returns the steps and
   the exact argument lists, warnings and the board's recovery steps. Nothing is written and the board is not reset (the chip
   probe waits until after the confirmation: `chip_probe: "after confirmation"`). The plan does not repeat the board name:
   the app shows it, and the person types it.
2. **run** (`flash run`, `POST /v1/flash/run`): checks, then requires in order the typed board name, the agent's consent
   (when the actor is an agent) and, for an image its publisher does not sign, `ack_unsigned` (`--ack-unsigned`). Only then
   is the board probed (reset) and every check repeated with the real chip, and then
   1. writes a `flash-start` line to the flash log (if the log cannot be written, nothing is flashed),
   2. backs up: Meshtastic `--export-config`, else MeshCore `infos` (identity and radio details, not a restorable export).
      If no firmware answers the result says `not_possible` and the flash is refused unless `allow_no_backup`.
      A board already in its UF2 bootloader has nothing to save,
   3. optionally erases first (`erase`, ESP32 only),
   4. flashes with the official tool as an argument list: ESP32 `esptool --chip C --port P --baud 460800 write_flash -z OFFSET FILE ...`;
      UF2 copy to the drive; nRF52 serial DFU `adafruit-nrfutil dfu serial --package ZIP -p P -b 115200 --singlebank --touch 1200`,
   5. verifies by waiting for the board and asking it (`meshtastic --info`, `meshcore-cli infos`, `rnodeconf PORT -i`):
      `confirmed`, `unconfirmed` (no port or no tool; not a failure) or `failed` (answered wrongly, or not at all),
   6. restores: when the new firmware is the same network as the backup (Meshtastic to Meshtastic, for example to roll back
      a version) the saved config is re-applied with `meshtastic --configure`. Across networks nothing is applied; the backup
      is kept (`backups`) for switching back, and the result says `kept_for_switching_back`.
   7. writes `flash-end`.
   On failure the result has `recovery`: the per-board bootloader button sequence plus general advice.

Images with `unsigned_by_publisher` (Meshtastic, MeshCore) flash only with `ack_unsigned`; the refusal and the plan warning say
exactly what was verified (the file matches the sha256 the release host lists, and where it came from) and what was not (the
project signs nothing, so nothing proves it is what the project built). An entry with no checksum from anyone
(`verification: unsigned-by-publisher`) is refused as `unverified` and never flashed.

Refusals (HTTP 200 with `ok:false`, CLI exit 1), all logged: `no_such_device`, `no_such_firmware`,
`licence_required`, `hash_mismatch`, `board_mismatch` (the image's board is not among the device's candidates),
`chip_mismatch`, `probe_required` (after confirmation), `tool_missing`, `not_in_bootloader`, `confirmation_required`, `consent_required`,
`bad_request`, `log_unavailable`, `unsigned_ack_required`, `unverified`, `owner_required` (403). Failure stages: `backup`, `erase`, `flash`; the verify result is separate.

Tools: `esptool` (or `esptool.py`), `meshtastic`, `adafruit-nrfutil`, `meshcore-cli`, `rnodeconf`, found on PATH or via
`TAOS_FW_ESPTOOL`, `TAOS_FW_MESHTASTIC`, `TAOS_FW_NRFUTIL`, `TAOS_FW_MESHCORE_CLI`, `TAOS_FW_RNODECONF`.
`TAOS_FW_VERIFY_WAIT_MS` (default 25000) is how long to wait for the board after a flash.

### Agents

An agent flashes only with the person's consent: `consent grant --agent ID --by NAME --minutes N` (1 to 1440),
revoked with `consent revoke`. A request with `actor: "agent:<id>"` without a live grant is refused
(`consent_required`). A grant is not the confirmation: the board name still has to be typed, by the person on screen.
Every attempt is in the flash log with its actor.

## Local JSON API

`taos-firmware serve [--port N]` (default 7421; 0 picks a free port and prints `{"ok":true,"port":N}`).
Binds 127.0.0.1 only. Every request needs `Authorization: Bearer <contents of api-token>`; requests with an
`Origin` header or a Host other than `127.0.0.1:<port>` / `localhost:<port>` are refused (403), so a web page cannot call it.
The token alone is not enough for the three actions that carry the person's authority: `POST /v1/licences/accept`,
`POST /v1/consent/grant` and `POST /v1/flash/run`. Each needs `owner_nonce` in the body: a single-use, five-minute nonce scoped
to that action and subject (`licence.accept` + network, `consent.grant` + agent id, `flash.run` + `device|entry`). The app mints
it when the person acts on screen, by connecting to the unix socket `owner.sock` in the data directory (mode 0600, directory 0700)
and sending `{"action":"...","subject":"..."}`; the server checks the kernel-reported peer uid is the owner's (`SO_PEERCRED`).
`taos-firmware owner mint --action A --subject S` does the same for scripts. Limit: another process running as the same
user can also reach that socket, so keeping an agent away from it takes a separate uid or a sandbox that hides the data
directory. The CLI is the person's own shell and has no such check.

Request bodies are JSON (max 64 KB); replies are JSON, `ok` always present. One flash or refresh runs at a time
(a second gets HTTP 409 `busy`). `refresh` and `flash/run` block until finished (a flash can take minutes).

| method and path | body / query | reply |
|---|---|---|
| GET /v1/status | | `{service, version, library_entries}` |
| GET /v1/boards | | boards, chips, networks, recovery text |
| GET /v1/library | `?board=&network=` | `{entries:[manifest entries]}` |
| POST /v1/library/refresh | `{network?, board?}` | `{reports:[{network, status, detail, fetched, skipped, errors, retry_after_epoch}]}`; status `ok`, `licence_required`, `offline`, `rate_limited`, `error` |
| GET /v1/licences | | `{accepted:[...], pending:[{network, name, licence_id, licence_url}]}` |
| POST /v1/licences/accept | `{network, by, owner_nonce}` | `{newly_accepted, acceptance}` (400 if `by` is empty) |
| GET /v1/devices | `?probe=1` | `{devices:[...]}` |
| GET /v1/devices/{id}/firmware | `?probe=1` | `{device, firmware:[entries with usable_now]}` |
| POST /v1/flash/plan | `{device, entry}` | `{ok, plan}` or `{ok:false, code, message}` |
| POST /v1/flash/run | `{device, entry, confirm, owner_nonce, actor?, ack_unsigned?, allow_no_backup?, erase?, restore_from?}` | `{ok, stage, steps, backup, verify, restore, recovery?}` or a refusal |
| GET /v1/backups | | `{backups:[{id, board, network, file, taken_at, replaced_by}]}` |
| GET /v1/log | `?limit=50` | `{log:[...]}` |
| GET /v1/consent | | `{grants}` |
| POST /v1/consent/grant | `{agent, by, minutes, owner_nonce}` | `{grant}` |
| POST /v1/consent/revoke | `{agent}` | `{revoked}` |

## Not done, or not verified

* No flash has ever touched a real radio: every flash test uses fake `esptool`/`meshtastic`/`nrfutil` scripts and fake
  USB and UF2 trees. The library side was run once against the real GitHub releases (all three networks, hash checks
  passing, about 95 MB for the seven boards and two versions each). USB ids and UF2 `Board-ID` strings are from
  documentation, not checked on boards. The post-flash handshake commands for MeshCore and RNode are unchecked.
* RNode: the flash writes the firmware at the documented offsets, but `rnodeconf` also provisions the EEPROM
  (product, model, signature, firmware hash). That step is not done here, so a freshly flashed RNode may report
  `unconfirmed` or `failed` until it is provisioned with `rnodeconf`.
* Settings do not carry across networks yet (no apply of region or channel to MeshCore or RNode); they are kept for switching back.
* There is no lock between two processes: the CLI and a running API server can both start a flash. Use one of them at a time.
* The actor is declared by the caller; binding it to a real identity needs the app or the OS consent layer.
* Meshtastic `.mt.json` md5 values are not checked (the zip's sha256 is). The API has no cancel.
* RAK4631 style boards that need a DFU package but only have a UF2 (no OTA zip in the release) must be put in
  the bootloader by the person (the recovery text says how).
