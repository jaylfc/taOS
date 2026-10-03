### Fixed

- Update-check tests: give `_FakeProc` a `returncode` attribute (matching the real subprocess contract) and mock `check_preflight` so the tracked-branch update-check test reaches the git subprocess path instead of failing early.
- Assert on `captured_proc.returncode == 0` so the attribute is load-bearing rather than merely present.
- Use `lambda *a, **k: []` for the `check_preflight` mock so it is signature-agnostic.

**RED-FIRST**

```
$ python3 -m pytest tests/test_update_channel_routes.py::TestUpdateCheckFollowsTrackedBranch::test_update_check_follows_tracked_branch_pref -v
...
FAILED tests/test_update_channel_routes.py::TestUpdateCheckFollowsTrackedBranch::test_update_check_follows_tracked_branch_pref
...
AttributeError: '_FakeProc' object has no attribute 'returncode'
tinyagentos/routes/settings.py:631: AttributeError
============================= 1 failed in 4.52s =============================
```

**GREEN**

```
$ python3 -m pytest tests/test_update_channel_routes.py tests/test_docs_only_update.py -v
...
14 passed in 18.85s
```

**Sweep results**

`git grep -n "class _FakeProc|FakeProc" tests/` shows every fake proc on the update-check path already carries a `returncode` attribute:

- `tests/test_update_channel_routes.py:158` — `returncode=0` on `_FakeProc.__init__`
- `tests/test_docs_only_update.py:109` — `returncode=0` on `_FakeProc.__init__`
- `tests/test_docs_only_update.py:175` — `returncode=0` on `_FakeProc.__init__`
- `tests/test_backend_services.py:74` — `returncode` set in `_FakeProc.__init__`
- `tests/test_litellm_process_removed.py:77` — `returncode = None` on `_FakeProc`
- `tests/test_opencode_runtime.py:123` — `returncode = None` on `_FakeProcess`
- `tests/test_q2_1_security_sweep.py:148` — `returncode = None` on `_FakeProcess`
- `tests/scripts/test_check_all_skip.py:324` — `returncode = 1` on `FakeProc`
- `tests/test_gpg_verify.py:333` — `returncode = 1` on `FakeProc`
- `tests/test_desktop_rebuild.py:178` — `returncode = 1` on `FakeProc`

No additional test-file changes required.
