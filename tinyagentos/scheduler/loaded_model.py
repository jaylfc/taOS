"""loaded_model.py -- resident-model records for the core-aware scheduler.

A LoadedModel represents a model that is currently resident on a backend
(memory allocated, InferenceSession open). The scheduler maintains a
registry of these records and uses them for both eviction decisions and
resource-hold accounting.

Priority classes determine the order in which the scheduler targets
residents for shrink-reload or eviction when a new load creates pressure.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Priority class constants
# ---------------------------------------------------------------------------

class PriorityClass:
    """Symbolic names for the three resident priority levels.

    Higher value means the scheduler will target this model for
    shrink/eviction before lower values.

      always_resident (0) -- pinned, never shrunk or evicted.
      interactive     (1) -- default for user-facing agents.
      background      (2) -- ingest / batch; shrunk and evicted first.
    """
    ALWAYS_RESIDENT = "always_resident"
    INTERACTIVE = "interactive"
    BACKGROUND = "background"


PRIORITY_CLASSES = [
    PriorityClass.ALWAYS_RESIDENT,
    PriorityClass.INTERACTIVE,
    PriorityClass.BACKGROUND,
]

# Numeric rank used for comparison: higher rank -> evict first.
_PRIORITY_RANK: dict[str, int] = {
    PriorityClass.ALWAYS_RESIDENT: 0,
    PriorityClass.INTERACTIVE: 1,
    PriorityClass.BACKGROUND: 2,
}


def priority_rank(priority: str) -> int:
    """Numeric rank for a priority string. Unknown strings map to 0.

    Used for victim selection: pick the resident with the highest rank
    first (background before interactive; never always_resident).
    """
    return _PRIORITY_RANK.get(priority, 0)


# ---------------------------------------------------------------------------
# Model classes and per-class idle TTLs (#62)
# ---------------------------------------------------------------------------

class ModelClass:
    """Symbolic names for the model classes in issue #62's TTL table.

    The class decides how long a resident may sit idle before the
    scheduler's TTL sweep unloads it. Spellings reuse the capability
    names the scheduler already uses (Capability in types.py) where
    one exists.
    """

    IMAGE_GENERATION = "image-generation"
    SPEECH_TO_TEXT = "speech-to-text"
    TEXT_TO_SPEECH = "text-to-speech"
    EMBEDDING = "embedding"
    RERANKING = "reranking"
    QUERY_EXPANSION = "query-expansion"
    CHAT_SMALL = "chat-small"    # chat, <=4B params
    CHAT_LARGE = "chat-large"    # chat, >=7B params (and unparseable sizes)
    REASONING = "reasoning"


#: Default idle TTL per model class, in seconds -- issue #62's table.
DEFAULT_MODEL_TTLS: dict[str, float] = {
    ModelClass.IMAGE_GENERATION: 300.0,   # Image gen (SD/Flux/etc.) 5 min
    ModelClass.SPEECH_TO_TEXT: 120.0,     # STT / TTS 2 min
    ModelClass.TEXT_TO_SPEECH: 120.0,     # STT / TTS 2 min
    ModelClass.EMBEDDING: 30.0,           # Embedding 30 sec
    ModelClass.RERANKING: 15.0,           # Reranker 15 sec
    ModelClass.QUERY_EXPANSION: 15.0,     # Query expansion 15 sec
    ModelClass.CHAT_SMALL: 600.0,         # Chat (<=4B) 10 min
    ModelClass.CHAT_LARGE: 1800.0,        # Chat (>=7B) 30 min
    ModelClass.REASONING: 1800.0,         # Reasoning 30 min
}

# Backend types whose residents are a single media class regardless of
# the model name (mirrors BACKEND_CAPABILITIES in backend_catalog.py).
_IMAGE_GEN_BACKEND_TYPES = frozenset({"sd-cpp", "comfyui"})
_STT_BACKEND_TYPES = frozenset({"whisper"})
_TTS_BACKEND_TYPES = frozenset({"kokoro", "moss"})

# Backend types that serve chat-shaped traffic; their residents are
# classified by model name below. Any other non-empty backend type
# (e.g. iopaint, flux-fill) is not in #62's table and classifies as ""
# -- no TTL, never idle-evicted.
_CHAT_BACKEND_TYPES = frozenset({
    "rkllama", "ollama", "llama-cpp", "llama-swap", "vllm", "exo",
    "mlx", "lmstudio", "openai", "anthropic", "hailo-ollama",
})

# Parameter-count marker in a model name: "qwen2.5-7b", "llama-3.1-8b",
# "qwen3:0.6b". The separator requirement keeps "gpt-4o" and bare
# version numbers ("3.1") from matching.
_PARAM_COUNT_RE = re.compile(r"[-_:](\d+(?:\.\d+)?)b(?![a-z0-9])")


def _param_count_b(model_id: str) -> Optional[float]:
    """Parameter count in billions parsed from a model name, or None."""
    match = _PARAM_COUNT_RE.search(model_id)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def classify_model(model_id: str, backend_type: str = "") -> str:
    """Classify a resident into a ModelClass for TTL purposes.

    Backend type decides first for single-class media backends (image
    generation, STT, TTS). Chat-serving backends classify by model
    name: the utility models (embedding, reranker, query expansion)
    and reasoning models by name marker, and chat models by the
    parameter count in the name (<=4B is CHAT_SMALL; anything larger
    -- and any chat model whose size cannot be parsed -- is
    CHAT_LARGE, the conservative TTL). The 4B<size<7B gap in #62's
    table resolves to CHAT_LARGE.

    Returns "" for backend types outside #62's table: no TTL applies
    and the resident is never idle-evicted.
    """
    bt = (backend_type or "").strip().lower()
    if bt in _IMAGE_GEN_BACKEND_TYPES:
        return ModelClass.IMAGE_GENERATION
    if bt in _STT_BACKEND_TYPES:
        return ModelClass.SPEECH_TO_TEXT
    if bt in _TTS_BACKEND_TYPES:
        return ModelClass.TEXT_TO_SPEECH
    if bt and bt not in _CHAT_BACKEND_TYPES:
        return ""
    name = (model_id or "").lower()
    if "query-expansion" in name or "query_expansion" in name:
        return ModelClass.QUERY_EXPANSION
    if "rerank" in name:
        return ModelClass.RERANKING
    if "embed" in name:
        return ModelClass.EMBEDDING
    if "deepseek-r1" in name or "qwq" in name or "reasoning" in name:
        return ModelClass.REASONING
    size = _param_count_b(name)
    if size is not None and size <= 4.0:
        return ModelClass.CHAT_SMALL
    return ModelClass.CHAT_LARGE


# ---------------------------------------------------------------------------
# LoadedModel record
# ---------------------------------------------------------------------------

@dataclass
class LoadedModel:
    """A model currently resident on a backend.

    Attributes:
        model_id: stable model identifier matching the backend catalog.
        backend: backend type string, e.g. "rkllama", "llama-cpp", "vllm".
        memory_mb_used: memory this model holds on the device in MB.
        resource_holds: subset of the backend's resource shape currently
            claimed by this model. For RK3588 NPU this is
            ``{"cores": [0, 1, 2]}``. For CPU/CUDA backends it is
            ``{}`` (empty -- no discrete allocation to track).
        tp_mode: tensor-parallel mode baked into the InferenceSession at
            load time. "all" means all available cores; "0,1" means cores
            0 and 1; "0" means core 0 only; "" for backends that do not
            use tp_mode.
        loaded_at: monotonic epoch (time.time()) when this model was
            loaded.
        last_used_at: monotonic epoch of the most recent inference
            request on this model. Used for LRU eviction ordering.
        priority: one of PRIORITY_CLASSES.
        pinned: when True the model is treated as always_resident
            regardless of the priority field.
        model_class: a ModelClass value pinned by the wiring code when
            it knows the class; "" means classify from model_id +
            backend type at TTL lookup time (#62).
    """

    model_id: str
    backend: str
    memory_mb_used: int
    resource_holds: dict[str, Any] = field(default_factory=dict)
    tp_mode: str = ""
    loaded_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    priority: str = PriorityClass.INTERACTIVE
    pinned: bool = False
    in_use_count: int = 0
    model_class: str = ""

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def cores_held(self) -> list[int]:
        """NPU core indices held by this model, or empty list."""
        return list(self.resource_holds.get("cores", []))

    def gpu_ids_held(self) -> list[int]:
        """GPU device IDs held by this model, or empty list."""
        return list(self.resource_holds.get("gpu_ids", []))

    def is_always_resident(self) -> bool:
        return self.pinned or self.priority == PriorityClass.ALWAYS_RESIDENT

    def effective_priority_rank(self) -> int:
        """Numeric eviction priority. Always-resident models return -1
        so they are never selected as victims."""
        if self.is_always_resident():
            return -1
        return priority_rank(self.priority)

    def touch(self) -> None:
        """Update last_used_at to now. Call on every inference request."""
        self.last_used_at = time.time()

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "backend": self.backend,
            "memory_mb_used": self.memory_mb_used,
            "resource_holds": self.resource_holds,
            "tp_mode": self.tp_mode,
            "loaded_at": self.loaded_at,
            "last_used_at": self.last_used_at,
            "priority": self.priority,
            "pinned": self.pinned,
            "model_class": self.model_class,
        }
