### Fixed

- Files app: the Photos location can no longer delete itself. `DELETE` on the
  location root (an empty path, a `.`, or a path that walks back up like
  `sub/..`) resolved to the Pictures folder itself and moved the whole folder,
  camera roll included, into the trash. Those deletes now answer `400 Invalid
  path` and leave the folder and its files where they are.
- Files app: a broken symlink inside the Pictures folder no longer takes the
  whole Photos listing down. An entry that cannot be stat'ed (its target was
  moved off the device) is skipped; the rest of the folder still lists.
- Files app: a shared folder may be named `photos` again. Built-in location ids
  are namespaced in the Files routing, so a shared folder called `photos` and
  the built-in Photos location each keep their own rows and endpoints instead of
  the shared folder being served by the built-in one it collided with.

### Added

- The Photos location belongs to the machine owner until photo folders are
  per-user: the owner's session and admins can open it, any other signed-in
  account gets no Photos entry in the Files sidebar and `403` from
  `/api/photos/*`. An agent calling with a local token may list and download
  camera photos and is refused on delete and restore, so the camera roll stays
  read-only for agents.
- Deleting a camera photo now goes to a restorable trash scope and can be put
  back: `GET /api/photos/trash` lists what was deleted and `POST
  /api/photos/trash/{id}/restore` moves it back under the Pictures folder it
  came from. Those two routes are API-only for now; the Files recycle bin view
  does not list the Photos scope yet (see `docs/runbooks/recycle-bin.md`).
