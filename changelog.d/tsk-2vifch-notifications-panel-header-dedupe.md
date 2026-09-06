### Fixed
- The Notifications settings panel no longer duplicates its `<section>`/`<h2>`/description chrome across the loading, error, and loaded render branches; the chrome now lives in a single return, and the loading state's heading spacing unifies with the error and loaded states (the loader was the `mb-5` outlier).
