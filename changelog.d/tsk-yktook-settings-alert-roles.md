### Fixed
- Error and failure messages in the Updates, Logs, Themes, and Users settings panels are now announced to screen-reader users via `role="alert"` live regions, matching the pattern already used in the Notifications and Account panels. Routine success/progress text is intentionally left without a live region so it does not train users to ignore alerts.
