### Fixed

- Intel GPU stats: fixed regressions from PCI-based deduplication (#4419)
  - AMD: prefer ROCm VRAM/frequency over sysfs when both sources present; merge per-field
  - Utilisation 0 no longer becomes null (explicit `is None` checks instead of `or`)
  - GPUs without PCI address fall back to vendor-index matching
  - PCI address extraction uses LAST `0000:` component (correct for devices behind bridges)
  - xe driver sysfs paths cited from kernel docs (docs.kernel.org gpu/xe/xe_gt_freq, drivers/gpu/drm/xe/xe_gt_sysfs.c)
  - Fixed stray comment dedent in hardware.py
