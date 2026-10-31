### Fixed
- Excalidraw image loading now retries after a failed fetch or read error by tracking in-flight and loaded file IDs separately
- Accessible element list now includes image `alt` text from payload when text/title/label are absent
- Restored explanatory comments and eslint-disable directive removed in previous commit
