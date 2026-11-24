### Fixed
- Sheet now stays open on switched state after successful 200 API response, showing "Switching to <name>. To come back, open Return to taOS there." instead of returning to confirm buttons
- Vitest `confirm flow calls API once` assertion updated to expect `{confirm:true, force:false}` in request body
- Added vitest for success text appearing after switch, Tab/Shift+Tab trap focusing within sheet, and Switch anyway resending with force true when drain fails
- Added Python integration auth tests through real app + middleware: member session 403, local token 403, agent registry token 403, admin 200
