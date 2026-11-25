### Changed

- taOSmd is now pinned to 0.5.24 (from 0.4.0), with the optional ONNX runtime kept so the default local embedder still works. A memory store written by 0.4.0 opens as is under 0.5.24: its schema steps apply on open, every memory is kept, and the float32 vector conversion stays an explicit choice that never runs on its own. An upgrade test opens a store written by a real 0.4.0 and checks the read-only `taosmd migrate --probe` result (state old, exit 10).
