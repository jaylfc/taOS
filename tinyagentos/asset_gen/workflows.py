"""Curated ComfyUI workflow templates + parameterisation for asset generation.

Slice 1 ships one real, minimal workflow: SDXL text->image (``texture_sdxl.json``,
a standard checkpoint -> CLIP encode -> KSampler -> VAE decode -> save graph in
ComfyUI's API format). ``build_texture_workflow`` loads the template and stamps
in the caller's prompt, size, seed, and checkpoint, returning a fresh dict ready
for :meth:`ComfyUIClient.generate`.

Tileable/seamless textures: ComfyUI core has no asymmetric-tiling toggle without
a custom node (e.g. the "Seamless" / circular-padding nodes), so Slice 1 nudges
the diffusion toward a repeating result through the prompt only. Wiring true
asymmetric tiling is a documented follow-up (TODO: seamless custom node).
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

_WORKFLOWS_DIR = Path(__file__).parent / "workflows"

# Node ids in texture_sdxl.json (kept in one place so the template and the
# parameteriser never drift).
_POSITIVE_NODE = "6"
_NEGATIVE_NODE = "7"
_LATENT_NODE = "5"
_SAMPLER_NODE = "3"
_CHECKPOINT_NODE = "4"

_DEFAULT_NEGATIVE = "blurry, low quality, watermark, text, signature"


@lru_cache(maxsize=None)
def load_template(name: str) -> dict:
    """Load a checked-in workflow template by name (without the .json suffix).

    Cached: the set of templates is fixed and small, and every caller goes
    through ``build_texture_workflow``, which deep-copies before mutating, so
    the cached dict is never mutated in place.
    """
    path = _WORKFLOWS_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _tileable_hint(prompt: str) -> str:
    """Append a seamless-texture hint if not already implied by the prompt."""
    if "seamless" in prompt.lower() or "tileable" in prompt.lower():
        return prompt
    return f"{prompt}, seamless tileable texture, repeating pattern"


def build_texture_workflow(
    *,
    prompt: str,
    width: int = 512,
    height: int = 512,
    seed: int = 0,
    tileable: bool = False,
    checkpoint: Optional[str] = None,
    negative_prompt: str = _DEFAULT_NEGATIVE,
    template: str = "texture_sdxl",
) -> dict:
    """Return a ready-to-submit ComfyUI workflow for a text->image texture.

    A deep copy of the template is returned so concurrent callers never share
    (and mutate) the same graph.
    """
    workflow = copy.deepcopy(load_template(template))

    positive = _tileable_hint(prompt) if tileable else prompt
    workflow[_POSITIVE_NODE]["inputs"]["text"] = positive
    workflow[_NEGATIVE_NODE]["inputs"]["text"] = negative_prompt
    workflow[_LATENT_NODE]["inputs"]["width"] = int(width)
    workflow[_LATENT_NODE]["inputs"]["height"] = int(height)
    workflow[_SAMPLER_NODE]["inputs"]["seed"] = int(seed)
    if checkpoint:
        workflow[_CHECKPOINT_NODE]["inputs"]["ckpt_name"] = checkpoint
    return workflow
