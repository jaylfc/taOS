### Fixed

- Orb app downloads now land durably: the streamed temp file is fsynced before the atomic rename, the destination directory is fsynced, fetch_manifest serializes verify-record-cache under one lock so a stale manifest is never cached behind a newer serial, downloads write chunks off the event loop with a per-read idle timeout plus total cap, and stored file names include a sha256 prefix so versions sharing a basename no longer overwrite each other.
