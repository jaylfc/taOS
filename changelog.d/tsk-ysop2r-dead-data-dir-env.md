### Fixed

- Fix the per-test `data/` mutation guard in `tests/conftest.py`: use `request.node.nodeid` instead of the non-existent `request.nodeid`, snapshot `(st_mtime_ns, st_size)` instead of just `st_mtime`, and make the guard xdist-aware by deferring reports to session end when `PYTEST_XDIST_WORKER` is set.
- Move confirmed offenders to `tmp_path`: `tests/test_contacts_peer.py::TestPeerEnvelope::test_build_envelope_structure` sets `TAOS_DATA_DIR` before calling `build_envelope`, and three `tests/test_deployer.py::TestBackgroundDeploy` tests redirect `taosmd.agents` to a temp `AgentRegistry`.
- Use `monkeypatch.setattr` in `tests/test_data_dir_resolution.py` instead of hand-assigning `PROJECT_DIR` on the module.
