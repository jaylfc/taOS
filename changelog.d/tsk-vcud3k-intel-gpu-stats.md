### Added

- Intel integrated GPU cards in Activity now report real stats instead of "Stats unavailable": GPU load from i915/xe RC6 residency in sysfs (no root or perf needed), current and max GPU frequency, the card's proper name resolved from pci.ids (e.g. "Intel UHD Graphics 750" instead of raw "[0x4c8a 0x8694]"), and VRAM shown as "Shares system memory" since iGPUs share system RAM.
