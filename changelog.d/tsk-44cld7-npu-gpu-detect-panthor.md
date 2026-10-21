### Fixed
- resource_manager._detect_npu(): now delegates to hardware._detect_npu() instead of checking /sys/class/misc/mali0, so NPU detection works correctly under the panthor GPU driver
- hardware.py Mali GPU detection: path 2 now also matches 'panthor' in the driver name, so panthor-driven GPUs are correctly identified as mali type