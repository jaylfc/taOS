# Interim record store and the taOSmd mapping

taOSmd's record API is not available to this engine yet, so records go to an append-only JSONL store under the
person's data directory until it is. The store is designed to be replayed into taOSmd later without loss.

## Files (all in the data directory)

| File | Content |
|---|---|
| `records.jsonl` | one JSON record per line: every frame in and out, plus send-state and refused-send records |
| `identities.jsonl` | identity events: contact create/update, radio assign, link, told, locations hidden |
| `grants.jsonl` | grant and revoke events |

## Rules

* Append only. A line is written with one `write_all` and `sync_data` before the engine reports the record stored.
  Nothing is rewritten, trimmed or deleted.
* A write failure is returned to the caller and shown in `health`; the record stays in memory, in order, and every
  later write retries it first. A send whose write fails is refused and not left pending as if queued.
* A crash mid-write leaves a last line without a newline. On open that fragment is moved to
  `records.jsonl.torn-N` (kept) and the log is cut back to the last complete line. Any other unreadable line stops
  start-up with its line number rather than being skipped.
* Record ids are the first 16 bytes (32 hex chars) of a SHA-256 over direction, network, radio, raw bytes, host
  time, a per-run sequence number and (for state records) the record they update. Replaying a line keeps its id, so
  an import into taOSmd is idempotent on `id`.
* New fields are only ever added with defaults; an old line stays readable (tested).
* The data directory is the person's. Identity events and grants are the registry state: replay them in order.

## Mapping to taOSmd (spine rules: owner on every row, raw kept, state appended)

| Interim | taOSmd |
|---|---|
| `owner` | the owner identity of the record (no record without one) |
| `dir=in|out`, `raw_hex` | the raw frame record: exact bytes, transport, radio, time, signal, owner. Never trimmed |
| `kind=text|position|nodeinfo|telemetry|ack` and `text`, `position`, `detail` | the decoded message or position record derived from the raw record (`id` links them) |
| `dir=state`, `ref_id`, `state`, `note` | state changes appended to the outbound record |
| `kind=send_refused`, `principal` | the audit entry for an agent action, with the grant outcome |
| `sender`, `recipient`, `observer` | identity attribution at write time |
| `identities.jsonl` | identity registry events (assignments, contacts, links) |
| `grants.jsonl` | consent records |
| `locations_hidden` event | a visibility tombstone: the raw records remain until the owner rules on deletion |

Replay order is file order. Import is verified by comparing ids; the interim files are kept until the count and ids
match.
