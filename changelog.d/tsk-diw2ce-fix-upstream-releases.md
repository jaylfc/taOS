### Fixed

- Docker Hub tag lookup now follows pagination (up to 10 pages), so newer tags on later pages are detected
- Docker Hub pagination now stops at external hosts to prevent security issues
- The baseline version comparison now uses the current image pin first, avoiding stale cache issues
