### Fixed
- Agents Requests tab: action errors no longer hide the list; they now appear as a dismissible banner above the rows
- Empty-state heading now matches the active filter ("No requests" for all, "No pending requests" for pending)
- Requests tab strip now has complete ARIA tab relationships (role=tablist/tab/tabpanel with aria-selected, aria-controls, aria-labelledby)
- Requests tab is now gated to admins and users who own at least one agent, preventing 403 errors for unauthorized users
