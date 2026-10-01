### Fixed
- `taos worker convert-to-lxc` now reports non-auth HTTP errors from the local controller without telling the user to log in. Only 401/403 responses trigger the "rejected the token / run taosctl login" message; other statuses like 404 or 500 are reported plainly. Also catches unreadable `config.yaml` (`OSError`) instead of crashing with a traceback.
