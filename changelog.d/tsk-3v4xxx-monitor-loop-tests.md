### Fixed

- Rewrite three `_monitor_loop` tests to drive the real `start()` path, make `emit_event` raise once, and assert the loop survives the exception and the crash is logged. Previously these tests passed on origin/dev because they bypassed `start()` and never triggered `emit_event`, so they did not exercise the recovery fix.
