### Changed

- Device TLS: narrow reuse-check exception handling. An unrelated `AttributeError` during cert/key load now propagates instead of silently regenerating the pair.