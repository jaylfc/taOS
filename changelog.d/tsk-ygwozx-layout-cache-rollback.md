### Fixed

- layout-store: when the unsaved-marker write fails, restore the previous localStorage value instead of removing it unconditionally, so re-picking the same choice (e.g. "off") survives a reload with the server offline instead of falling back to the default.
