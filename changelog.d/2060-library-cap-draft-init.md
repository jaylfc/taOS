### Fixed

- **Library cap draft initializes even when a quality edit came first (#2060)** — in the Library settings pane, editing the preferred quality before the initial settings load resolved blocked the storage-cap draft from initializing: the input kept showing the built-in default while the server held the persisted cap, and touching the cap field would then have saved the default over it. The mount load's edit guard is now per-field: a quality edit no longer blocks cap initialization, and only an actual cap edit blocks the cap draft.
