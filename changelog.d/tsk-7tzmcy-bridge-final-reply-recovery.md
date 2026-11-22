### Fixed
- Bridge final reply recovery now broadcasts a full `message` event (not just `message_edit`) so a client that never saw the original can render the reply, broadcasts the merged content_blocks list, reloads the row before re-dispatch, skips the edit when `final_content` is empty (never overwriting committed text), and null content-block ids no longer collide in the dedupe key.
