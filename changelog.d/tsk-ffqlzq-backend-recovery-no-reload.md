### Fixed

- Backend recovery no longer reloads the page (which closes all open windows). Instead, a `recoveredAt` counter in `BackendStatusContext` is incremented on each recovery, and apps use a new `useOnBackendRecovered` hook to re-fetch their data when the backend comes back up. Affected apps: Activity, Files, Notes, Models.
