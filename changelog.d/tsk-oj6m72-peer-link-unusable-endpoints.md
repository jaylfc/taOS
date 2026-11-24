### Fixed

- A peer link whose stored endpoint list holds no usable endpoint (bad scheme, no host, invalid port) is now repaired by a later friend accept that supplies valid endpoints, instead of keeping the unusable list forever. (#4256)
