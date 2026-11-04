```FAILED tests/test_hardware_wifi.py::test_scan_continues_on_adapter_failure_two_adapters - AssertionError
assert 502 == 202
E       502 = <starlette.responses.JSONResponse object at ...>.status_code

FAILED tests/test_hardware_wifi.py::test_forget_nm_unavailable_precheck_returns_503 - AssertionError
unexpected busctl call: ('call', 'org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager/Settings', 'org.freedesktop.NetworkManager.Settings', 'ListConnections')
```