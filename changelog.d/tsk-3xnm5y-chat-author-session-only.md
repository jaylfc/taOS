### Fixed

- `POST /api/chat/messages` now restricts the author_id check to browser sessions
  only. The check previously ran for every caller that was neither a device
  bearer nor a bound agent, which also caught the host local token
  (`via == "local_token"`) and other non-browser callers; those callers now
  keep today's dev behaviour of storing the body author as sent. The check
  applies only when `request.state.via == "session"`: author_type is forced to
  "user" and author_id must be missing, "user", or the caller's user_id, else 403
  `{"detail": {"error": "author_id must be the caller"}}`. A missing author_id
  and the literal "user" are both stored as the caller's user_id, so the sender
  can edit and delete their own message. A non-string
  author_id now returns that 403 instead of a 500.
