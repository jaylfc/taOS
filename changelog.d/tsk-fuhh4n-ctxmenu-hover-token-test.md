### Fixed
- replaced regex `.match()` extractions with `bgToken` helper in `contrast-relationships.test.tsx` so `itemHoverToken` is unprefixed like `menuSurface` and `dividerToken`, enabling actual equality checks and causing a missing hover class to fail the test
