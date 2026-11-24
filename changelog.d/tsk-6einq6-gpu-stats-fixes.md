### Fixed

- **xe: frequency file names** - Fixed xe GPU frequency file names from i915-style (`gt_act_freq_mhz`, `gt_cur_freq_mhz`, `gt_max_freq_mhz`) to correct xe names (`act_freq`, `cur_freq`, `max_freq`) under `.../tile0/gt0/freq0/`. Cited real document at docs.kernel.org gpu/xe/xe_gt_freq.
- **AMD: prefer ROCm per field** - When ROCm reports load, frequency and VRAM values, those now take priority over sysfs fallback. ROCm devices matched by PCI address when available, by vendor index only as fallback.
- **vram_percent TypeError fix** - Fixed `vram_percent` calculation to return `None` when `used` is `None` and `total` > 0, preventing `TypeError` from dividing `None` by an integer.
