### Fixed

- Guard-discrimination gate (`scripts/check_non_discriminating.py`): a guard on a
  METHOD target is no longer silently skipped. A method's source is indented, so
  `ast.parse` raised `IndentationError` and the uncheckable-shape check
  (mutation sites in a decorator or a default argument) was swallowed for every
  method; the source is now dedented before parsing, and a source the gate cannot
  parse is reported as ERROR (uncheckable) instead of passing. A cached target is
  also rejected at any layer of its decorator stack, so `functools.lru_cache`
  hidden under a `functools.wraps` decorator is no longer checked and reported
  NON-DISCRIMINATING.
