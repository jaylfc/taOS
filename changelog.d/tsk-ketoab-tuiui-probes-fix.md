```### Fixed

- probe3_frame_grid_readback.py: Fixed Test 1 to use first_match over the frame window instead of breaking after the first frame. Now properly checks all frames in the window for command output before failing with "no frame carried the command output".

- probe5_scroll_viewport.py: Fixed scroll sign in transcript. Changed "positive = back into history" to "positive = forward into history" to correctly describe scroll direction (positive lines parameter scrolls forward into history, not backward).

- probe2_input_bytes_typing.py: Updated transcript to list complete byte array [104, 101, 108, 108, 111, 10] (including newline byte). Changed documentation to reflect that the wire protocol carries all 6 bytes as integer array, not just the 5 bytes of "hello".

- probe4_detach_reattach_appid.py: Fixed to spawn a long-lived child process ("sleep 30") instead of quick-executing "echo hello". Added proper cleanup with conduit.kill() in finally block to prevent hanging child processes. This ensures correct AppId stability testing.

- probe1_socket_enumerate_spawn.py: Fixed README example output to match actual probe1 output ("Result: Input echoed in frame" instead of "Result: Input sent successfully").

- probe1_socket_enumerate_spawn.py and probe2_input_bytes_typing.py: Removed unused imports (json and socket from probe1, json from probe2) to clean up code and pass linting checks.

All tuiui probe fixes now have proper source assertions in the test suite that verify the changes are correct.
```