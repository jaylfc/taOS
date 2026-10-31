### Fixed

- Wire mDNS hostname choice into app.py: pass `data_dir` to `MdnsPublisher` constructor so `start()` is reached on every box start, and respect `config.mdns.get('enabled', True)` and `TAOS_MDNS` env var to skip publisher construction
- Off switch: app.py skips constructing the publisher and sets `app.state.mdns_publisher = None` when `config.mdns.get('enabled', True)` is false or `TAOS_MDNS == '0'`; `tinyagentos/config.py` parses `mdns` from YAML in `load_config` the same way `mdns_reflector` is parsed, and omits it from `to_dict` when it equals the default `{"enabled": True}`
- Card Decision 4: added `mdns_hostname` (string without trailing dot, or `null` when publisher is absent/not started) to existing `GET /api/version` JSON
- Persisting the name: replaced hand-rolled temp file + `os.replace` in `choose_hostname` with `tinyagentos.atomic_io.atomic_write_text`
- Tests: passed `data_dir=tmp_path` in `tests/test_mdns_publisher.py` and stubbed the probe with `_FakeAddressResolver`; added `test_version_endpoint_mdns_hostname_when_active` to verify `GET /api/version` carries `mdns_hostname` when publisher is active
- mDNS probe failure no longer persists `taos.local`; the next boot re-probes instead of claiming the name forever
- `config.mdns.enabled` now treats `0`, `false`, or `"0"` as disabled (not just `False`)
- Version endpoint tests run the lifespan to verify publisher construction is gated by `TAOS_MDNS`
- /api/version reports mdns_hostname only while the name is actually advertised