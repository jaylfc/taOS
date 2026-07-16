"""Game Studio offline asset generation.

Slice 1 (textures/sprites) drives a ComfyUI server to turn a text prompt into a
PNG the game references. The backend is tier-aware and reuses the same catalog
+ hardware-profile signals the Images Studio uses; see
``docs/design/game-studio-asset-generation.md``.
"""
