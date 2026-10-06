---
author: jaylfc
pr: #3521
status: merged
date: "2026-10-06"
title: "Device events: close stream on agents:read loss, emit close+open on decision replace"
project: taOS
---

Fixed device events stream handling:

- Fixed `_next_event_id()` function to increment event_id before returning it, ensuring close and open events during decision replacement don't share an ID
- Restored poll block indentation and semantics for proper agent upsert, remove, recap, and decision event handling
- Added scope loss handling for AGENTS_READ in device token recheck
- Added old decision id to `decision.close` payload for client correlation
- Updated documentation with decision.replace rule and scope loss description
- Added missing test `test_events_route_refuses_device_without_agents_read`
- Updated `test_scope_loss_closes_stream` to use `_ticking_clock` for deterministic behavior
- Enhanced `test_decision_replace_emits_close_then_open` to verify payloads contain correct decision ids