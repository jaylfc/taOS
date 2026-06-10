from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class WorkerInfo:
    name: str
    url: str                          # Worker's base URL
    hardware: dict = field(default_factory=dict)  # From hardware detection
    # Each entry's `url` is the worker-local probe address (e.g. http://localhost:11434).
    # Cross-host callers must route through the worker agent (worker.url) -- never dial backend url directly.
    backends: list[dict] = field(default_factory=list)  # Available inference backends; name is "type:port"
    models: list[str] = field(default_factory=list)     # Currently loaded models
    capabilities: list[str] = field(default_factory=list)  # embed, chat, rerank, image-gen, tts, etc
    status: str = "online"            # online | offline | busy
    last_heartbeat: float = 0
    registered_at: float = 0
    load: float = 0.0                 # 0-1 utilization estimate
    platform: str = ""                # linux | windows | macos
    tier_id: str = ""                 # catalog hardware tier, e.g. "x86-cuda-12gb"
    potential_capabilities: list[str] = field(default_factory=list)  # derived from catalog + tier
    # KV cache quantization support exposed as separate K and V type lists
    # plus a boundary-layer-protect flag. Research (NexusQuant llama.cpp#21591
    # plus Ziskind empirical) shows asymmetric K/V is the correct default:
    # keys need more bits than values because softmax amplifies key-side
    # noise, while values are linearly combined. Qwen2.5 breaks with turbo K
    # unless boundary layers are kept at fp16.
    #
    # Defaults to k=["fp16"], v=["fp16"], boundary=False so old workers that
    # predate these fields are treated as fp16-only.
    #
    # A worker running TheTom/llama-cpp-turboquant or a TurboQuant-capable
    # vLLM build will probe its backends and report what llama-cli's -ctk/-ctv
    # flags actually accept, e.g.
    #   k = ["f16", "bf16", "q8_0", "q4_0", "q5_0", "turbo2", "turbo3", "turbo4"]
    #   v = ["f16", "bf16", "q8_0", "q4_0", "q5_0", "turbo2", "turbo3", "turbo4"]
    #   boundary_layer_protect = True
    #
    # The legacy kv_cache_quant_support field is retained as a read-only
    # aggregate (union of k and v) for backwards compatibility with any
    # consumer that hasn't learned the split yet.
    kv_cache_quant_support: list[str] = field(default_factory=lambda: ["fp16"])
    kv_cache_quant_k_support: list[str] = field(default_factory=lambda: ["fp16"])
    kv_cache_quant_v_support: list[str] = field(default_factory=lambda: ["fp16"])
    kv_cache_quant_boundary_layer_protect: bool = False
    # Shortcut / worker-registry fields (Tasks 22-23)
    worker_url: str | None = None          # Public URL used by shortcut proxy
    signing_key: bytes = field(default_factory=bytes)  # HMAC key for ticket signing
    tls_cert_provider: str | None = None   # e.g. "letsencrypt", None = plain HTTP
    # LXC capacity fields (Task 1 — worker-as-LXC architecture)
    host_lan_ip: str | None = None          # Bare host's LAN IP
    storage_cap_bytes: int = 0              # Worker btrfs pool loopback max
    storage_used_bytes: int = 0             # Current actual usage across agent containers
    bytes_deduped_total: int = 0            # Cumulative bytes reclaimed by bees dedup
    worker_lxc_image_version: str | None = None  # Ubuntu image version, e.g. "ubuntu/24.04/amd64"
    degraded: bool = False
    degraded_reason: str | None = None
