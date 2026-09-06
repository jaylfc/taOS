### Fixed

- The deleted-symbols gate no longer crashes or mis-resolves on a `.py -> symlink`
  typechange. `_get_symbols_at_ref` now skips symlink/hardlink tar members (a symlink is
  not a real source file, and `tarfile.extractfile` raises `KeyError` on a dangling
  symlink target on Python 3.12+), and `_resolve_symbol` resolves a symlinked module to
  its real target while pinning the result inside the extracted merge tree — a symlink
  that escapes the tree (absolute target or `..`) is treated as not importable instead of
  re-entering the working tree and crashing or mis-reporting.
