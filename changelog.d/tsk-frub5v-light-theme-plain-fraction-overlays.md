### Fixed
- Light-theme white-overlay inversion: added missing `[class~=]` rules for 21 plain-fraction forms (bg-white/8, hover:bg-white/20, divide-white/5, data-[state=unchecked]:bg-white/10, etc.) that were invisible to the #2637 derived guard; widened the coverage test regex to catch both arbitrary-value and plain-fraction gaps going forward.
