### Fixed

- Phone UI audit (2026-11-16): Models app no longer stalls on "Loading models..." -- a rejected or empty `/api/models` response now ends the loading state and shows the error state with a Retry button (or the empty state on an ok empty list), with the spinner cleared in a `finally` block.
- Calendar: tapping a day now selects it (visible accent style + `aria-selected`) and the day panel/agenda renders for that date, showing "No events." when it has none.
- Cluster: on narrow widths the tab bar no longer overflows onto the "Add device" control -- tabs scroll horizontally inside their own container (`overflow-x-auto`, no wrap) and the "Add device" plus sort controls move to a second row below 600 px.
- Projects: the New Project dialog is now aligned to the top of the visible area with a max-height bound to the visual viewport and a scrollable inside, so the focused field and Create button stay visible above the on-screen keyboard on the mobile shell.
