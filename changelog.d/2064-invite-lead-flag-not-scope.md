### Changed

- The invite dialog no longer offers a "Make this agent the project lead"
  checkbox, and it no longer sends a fabricated `"lead"` scope at mint — `lead`
  is not a valid scope and previously caused the mint endpoint to reject the
  invite with a 400. Lead assignment now happens post-registration via
  `PATCH /api/projects/{id}/lead`; the dialog shows a note pointing the operator
  there instead.