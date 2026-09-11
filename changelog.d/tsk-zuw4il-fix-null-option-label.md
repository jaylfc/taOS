### Fixed
  - `_build_device_push_payload` now handles null option labels gracefully instead of raising TypeError when a notification row has `{"label": null, "value": "x"}`. The function coerces null labels to empty strings using `str(o.get("label") or "")` before slicing, preserving its contract of never raising.
