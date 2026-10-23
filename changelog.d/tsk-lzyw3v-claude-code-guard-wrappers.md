### Fixed
- Wrapper programs (`env`, `nice`, `nohup`, `timeout`, `xargs`, `sudo`, `doas`, `exec`, `command`, `builtin`, `time`, `stdbuf`, `setsid`, `ionice`, `chroot`, `busybox`) no longer hide the real program in the shell profile: the wrapper's key is recorded and the wrapped program is also checked.
- Hard refusal for `gh` and `git push` now also applies to parsed command keys, catching escapes like `g""h pr list` and `'gh' pr list`.
- The hook's `main()` now fails closed on any exception: it prints the refusal to stderr and exits with code 2.