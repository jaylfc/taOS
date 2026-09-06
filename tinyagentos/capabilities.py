from __future__ import annotations


CAPABILITIES = {
    "keyword-search": {"always": True},
    "agent-deploy": {"min_ram_mb": 2048},
    "chat-small": {"min_ram_mb": 4096},
    "chat-large": {"min_vram_mb": 8192},
    "embedding": {"min_ram_mb": 2048},
    "reranking": {"min_vram_mb": 4096, "or_npu": True},
    "semantic-search": {"min_ram_mb": 2048},
    "image-generation-cpu": {"min_ram_mb": 4096},
    "image-generation-gpu": {"min_vram_mb": 6144},
    "image-generation-npu": {"npu_type": ["rknpu"]},
    "video-generation": {"min_vram_mb": 6144},
    "tts": {"min_ram_mb": 2048},
    "stt": {"min_ram_mb": 2048},
    "music-generation": {"min_ram_mb": 4096},
    "lora-training": {"min_vram_mb": 8192},
    "full-training": {"min_vram_mb": 24576},
    "rknn-conversion": {"arch": "x86_64", "has_toolkit": "rknn"},
}

UNLOCK_HINTS = {
    "chat-large": "Add a GPU worker with 8GB+ VRAM to run larger models",
    "image-generation-gpu": "Connect a GPU with 6GB+ VRAM to unlock GPU image generation",
    "video-generation": "Connect a machine with 6GB+ GPU to enable video generation",
    "lora-training": "Add a GPU worker with 8GB+ VRAM to enable LoRA fine-tuning",
    "full-training": "Connect a machine with 24GB+ GPU for full model training",
    "rknn-conversion": "Add an x86 PC to your cluster to convert models for Rockchip NPU",
}


class CapabilityChecker:
    def __init__(self, hardware_profile, cluster_manager=None):
        self.hardware = hardware_profile
        self.cluster = cluster_manager

    def _get_total_resources(self) -> dict:
        """Aggregate resources from local hardware + all cluster workers."""
        resources = {
            "ram_mb": self.hardware.ram_mb,
            "vram_mb": 0,
            "npu_types": [],
            "architectures": [self.hardware.cpu.arch],
            "has_rknn_toolkit": False,
        }
        # Local GPU VRAM
        if self.hardware.gpu.type == "nvidia" and self.hardware.gpu.cuda:
            resources["vram_mb"] = max(resources["vram_mb"], self.hardware.gpu.vram_mb)
        if self.hardware.gpu.type == "amd" and self.hardware.gpu.rocm:
            resources["vram_mb"] = max(resources["vram_mb"], self.hardware.gpu.vram_mb)
        # Apple Silicon unified memory counts as VRAM (MLX-accelerated)
        if self.hardware.gpu.type == "apple":
            resources["vram_mb"] = max(resources["vram_mb"], self.hardware.gpu.vram_mb)
        # Local NPU
        if self.hardware.npu.type != "none":
            resources["npu_types"].append(self.hardware.npu.type)
        # Cluster workers
        if self.cluster:
            for worker in self.cluster.get_workers():
                if worker.status != "online":
                    continue
                hw = worker.hardware
                if isinstance(hw, dict):
                    worker_ram = hw.get("ram_mb", 0)
                    resources["ram_mb"] = max(resources["ram_mb"], worker_ram)
                    gpu = hw.get("gpu", {})
                    if gpu.get("cuda") or gpu.get("rocm") or gpu.get("type") == "apple":
                        resources["vram_mb"] = max(resources["vram_mb"], gpu.get("vram_mb", 0))
                    npu = hw.get("npu", {})
                    if npu.get("type", "none") != "none":
                        resources["npu_types"].append(npu["type"])
                    cpu = hw.get("cpu", {})
                    if isinstance(cpu, dict) and cpu.get("arch"):
                        resources["architectures"].append(cpu["arch"])
        return resources

    def is_available(self, capability: str) -> bool:
        req = CAPABILITIES.get(capability)
        if not req:
            return False
        if req.get("always"):
            return True
        res = self._get_total_resources()
        if "min_ram_mb" in req and res["ram_mb"] < req["min_ram_mb"]:
            return False
        if "min_vram_mb" in req:
            if res["vram_mb"] < req["min_vram_mb"]:
                # Check or_npu fallback
                if req.get("or_npu") and res["npu_types"]:
                    pass  # NPU can handle it
                else:
                    return False
        if "npu_type" in req:
            if not any(n in res["npu_types"] for n in req["npu_type"]):
                return False
        if "arch" in req:
            if req["arch"] not in res["architectures"]:
                return False
        return True

    def get_unlock_hint(self, capability: str) -> str | None:
        if self.is_available(capability):
            return None
        return UNLOCK_HINTS.get(capability)

    def get_all_capabilities(self) -> dict[str, dict]:
        """Return all capabilities with their availability and hints."""
        result = {}
        for cap in CAPABILITIES:
            result[cap] = {
                "available": self.is_available(cap),
                "hint": self.get_unlock_hint(cap),
            }
        return result
