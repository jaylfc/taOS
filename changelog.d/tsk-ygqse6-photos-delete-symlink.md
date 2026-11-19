### Fixed

- Files app: deleting an alias inside the Photos folder no longer deletes the
  wrong photo. A symlink inside the Pictures folder (`alias.jpg` pointing at
  `real.jpg`) moved the link's target into the recycle bin under the alias's
  name and left the alias dangling; the delete now moves the link itself, the
  target stays on disk, and a restore puts the link back.
- Files app: a Pictures folder that is itself a symlink (a camera roll moved
  to another disk) works as the Photos location again. Listing a subfolder of
  it returned a 500; the location now resolves the base directory itself.
- Files app: a recycle-bin restore can no longer write outside the folder the
  item was deleted from. A trash entry whose recorded original path resolves
  outside the location's root (a corrupt or hand-edited trash entry) is
  refused with `400` instead of restoring outside the folder.
- Files app: the Upload button no longer appears for the Photos location. The
  camera folder has no upload endpoint, so the button did nothing when
  clicked; it is now shown only for locations that accept uploads.
