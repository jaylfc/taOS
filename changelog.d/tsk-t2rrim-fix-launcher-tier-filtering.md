### Fixed
- Launcher tier filtering in `getLaunchableApps` now includes installed tier-5 optional apps (e.g., "coding-studio", "design-studio") when they are installed via the Store's optional-install flow. Previously these tier-5 apps were unconditionally excluded even when installed.
- Red-proven test added to verify the fix: tier-5 optional apps now appear in launcher listings when installed, and are excluded when not installed.

The fix ensures that the documented contract in "Re-fetch the installed set so the card flips to Open and the launcher surfaces the studio at once" is properly honored, allowing users who install tier-5 studios from the Store to immediately see them in the launcher.