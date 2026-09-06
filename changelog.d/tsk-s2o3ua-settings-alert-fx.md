### Fixed

- ThemesPanel now correctly announces HTTP errors via screen readers (e.g., 500 status codes) instead of silently converting them to an empty theme list. The error is properly caught and displayed in the alert region.
- UpdatesPanel now clears stale error messages when a successful update check occurs, preventing assistive tech from announcing outdated errors. The alert region is properly cleared on successful requests.