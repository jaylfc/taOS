### Fixed

- apps-store downloads now land under `<sha256[:16]>-<basename>` and assertions prove the hashed name; `durable_replace` is promoted off the event loop via `asyncio.to_thread`; temp files are cleaned up on `BaseException` (including cancellation); `fetch_manifest` runs its blocking verify/accept/cache inside `to_thread` under the serialising lock; manifest relative-URL segments are validated with `fullmatch` so a trailing newline is rejected; and the download deadline is a per-read idle timeout instead of a fixed 300 s total cap against the 1 GiB app size limit.
