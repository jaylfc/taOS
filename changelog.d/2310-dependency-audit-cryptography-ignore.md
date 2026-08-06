### Changed

- `dependency-audit` now ignores CVE-2026-69247, CVE-2026-69248 and
  CVE-2026-69249 in `cryptography` 48.0.1, each documented inline with its own
  reachability argument. None is reachable from taOS: two are confined to the
  X.509 chain-building verifier and one to PKCS#7 EnvelopedData decryption,
  and nothing in the dependency tree calls either API. There is no upgrade
  path, because every available `litellm[proxy]` pins `cryptography<49.0` and
  one of the three is not fixed until 50.0.0. The ignores are suppressions
  rather than fixes and carry a re-check command to drop them as soon as a
  fixed version resolves.
