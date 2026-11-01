### Fixed

- taOS no longer shows "New taOS build available" a minute after every page load. The build wrote a different id into version.json than into the app itself, so each page took its own build for a newer one.
