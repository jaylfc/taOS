### Fixed
- Fixed env -S split-string state leak where the word after the split string was swallowed, allowing `g""h` to bypass hard refusal
- Fixed env attached `-S<text>` and `--split-string=<text>` forms where split strings ending on a wrapper needing an operand (e.g., `timeout 60`) were not caught
- Fixed env clustered short options (`-iu FOO`) where `-u` taking a value was not handled in clusters
- Fixed sudo short options `-a`, `-c`, `-R` (and long forms `--auth-type`, `--login-class`, `--chroot`) missing from wrapper option value handling, allowing bypasses