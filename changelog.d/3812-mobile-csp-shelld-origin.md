### Fixed

- On a taOSmobile handset, apps opened from the dock now launch as their own window again instead of falling back to an in-page window. The Content-Security-Policy `connect-src` now allows exactly `http://127.0.0.1:6973` (taos-shelld) when the device class is mobile; desktop and unknown devices are unchanged.
