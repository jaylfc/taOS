### Fixed
- The light-theme compatibility layer now inverts arbitrary-value overlay
  utilities (`bg-white/[0.04]`, `border-white/[0.06]`, and their hover
  variants), not just the plain-fraction form (`bg-white/5`). The shared
  primitives (card, button, tabs) and ~126 app surfaces used the
  arbitrary-value form, which matched no attribute selector and so kept
  additive white overlays that vanished on light backgrounds.
