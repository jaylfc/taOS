### Fixed

- Wire mDNS hostname choice into app.py lifespan: pass `data_dir` to `MdnsPublisher` constructor so `start()` is reached on every box start (was causing TypeError and silent mDNS disappearance).
- Off switch: app.py skips constructing the publisher and sets `app.state.mdns_publisher = None` when `config.mdns.get('enabled', True)` is false or `env TAOS_MDNS == '0'`; config.py parses `mdns` from YAML like `mdns_reflector` and omits it from `to_dict` when default.
- `/api/version` carries `mdns_hostname` (string without trailing dot, or `null` when publisher absent/not started).
- Persist hostname via `tinyagentos.atomic_io.atomic_write_text` instead of hand-rolled `temp_file.write_text` + `os.replace`.
- Updated `tests/test_mdns_publisher.py` to pass `data_dir=tmp_path` in each test.
- Added tests: `TAOS_MDNS=0` suppresses publisher construction; `GET /api/version` carries `mdns_hostname`.