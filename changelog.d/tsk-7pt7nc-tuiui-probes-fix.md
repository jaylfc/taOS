### Fixed

- probe4_detach_reattach_appid.py: Fixed kill timing so the spawned app is killed after `rebind_by_meta` is evaluated, not before. Wrapped the entire probe body in try/finally and moved the kill to the outer finally over a fresh connection.
- probe5_scroll_viewport.py: Corrected scroll sign from "positive = forward into history" to "positive = back into history" with citation from `jaylfc/tuiui/src/ptyhost.rs:PtyHost::scroll`.
- probe2_input_bytes_typing.py: Removed unused `json` import. Transcript now lists the full byte array `[104, 101, 108, 108, 111, 10]` including the newline byte.
- probe3_frame_grid_readback.py: Match on the frame itself inside `first_match` instead of matching on lines and re-looking up the frame, preventing ANSI counts on the wrong frame.
- probe4_detach_reattach_appid.py: Updated `from source:` citation to `TuiuiConduit.rebind_by_meta`.
- docs/design/taos-tuiui-spike-findings.md: Added source-based citation for scroll sign and `display_offset` behavior.
