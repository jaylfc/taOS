from __future__ import annotations
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PlacementSuggestion:
    model_or_service: str
    current_worker: str | None
    suggested_worker: str
    reason: str
    improvement: str  # e.g. "3x faster inference", "frees 4GB on gaming-pc"


class ClusterOptimiser:
    def __init__(self, cluster_manager):
        self.cluster = cluster_manager

    def analyse(self) -> dict:
        """Analyse the cluster and suggest optimal resource placement."""
        workers = self.cluster.get_workers()
        if not workers:
            return {"suggestions": [], "summary": "No workers in cluster"}

        online = [w for w in workers if w.status == "online"]
        if len(online) < 2:
            return {"suggestions": [], "summary": "Need at least 2 online workers for optimisation"}

        suggestions = []

        # Sort workers by capability
        gpu_workers = []
        npu_workers = []
        cpu_workers = []

        for w in online:
            hw = w.hardware if isinstance(w.hardware, dict) else {}
            gpu = hw.get("gpu", {})
            npu = hw.get("npu", {})

            if gpu.get("cuda") or gpu.get("rocm") or gpu.get("type") == "apple":
                gpu_workers.append(w)
            elif npu.get("type", "none") != "none":
                npu_workers.append(w)
            else:
                cpu_workers.append(w)

        # Suggest embedding on weakest capable device
        if cpu_workers and (gpu_workers or npu_workers):
            weakest = min(cpu_workers, key=lambda w: w.hardware.get("ram_mb", 0) if isinstance(w.hardware, dict) else 0)
            suggestions.append(PlacementSuggestion(
                model_or_service="embedding-model",
                current_worker=None,
                suggested_worker=weakest.name,
                reason=f"{weakest.name} has enough resources for embedding — frees up GPU workers for larger tasks",
                improvement="Frees GPU for chat/image/video inference",
            ))

        # Suggest large chat model on most powerful GPU
        if gpu_workers:
            best_gpu = max(gpu_workers, key=lambda w: (w.hardware.get("gpu", {}).get("vram_mb", 0) if isinstance(w.hardware, dict) else 0))
            vram = best_gpu.hardware.get("gpu", {}).get("vram_mb", 0) if isinstance(best_gpu.hardware, dict) else 0
            if vram >= 8192:
                model = "qwen3-8b" if vram < 16384 else "qwen3-14b" if vram < 24576 else "qwen3-32b"
                suggestions.append(PlacementSuggestion(
                    model_or_service=model,
                    current_worker=None,
                    suggested_worker=best_gpu.name,
                    reason=f"{best_gpu.name} has {vram // 1024}GB VRAM — best for large model inference",
                    improvement="Fastest inference in the cluster",
                ))

        # Suggest image gen on GPU worker
        if gpu_workers:
            for gw in gpu_workers:
                vram = gw.hardware.get("gpu", {}).get("vram_mb", 0) if isinstance(gw.hardware, dict) else 0
                if vram >= 6144:
                    suggestions.append(PlacementSuggestion(
                        model_or_service="image-generation",
                        current_worker=None,
                        suggested_worker=gw.name,
                        reason=f"{gw.name} has GPU with {vram // 1024}GB VRAM",
                        improvement="Enable image and video generation",
                    ))
                    break

        # Suggest NPU for reranking if available
        if npu_workers:
            suggestions.append(PlacementSuggestion(
                model_or_service="reranking-model",
                current_worker=None,
                suggested_worker=npu_workers[0].name,
                reason=f"{npu_workers[0].name} has NPU — efficient for reranking",
                improvement="Low-power reranking, frees CPU/GPU",
            ))

        summary = f"{len(online)} workers online, {len(suggestions)} optimisation suggestions"

        return {
            "suggestions": [
                {"model": s.model_or_service, "current": s.current_worker,
                 "suggested": s.suggested_worker, "reason": s.reason, "improvement": s.improvement}
                for s in suggestions
            ],
            "summary": summary,
            "workers": [
                {"name": w.name, "status": w.status, "platform": w.platform, "load": w.load,
                 "capabilities": w.capabilities,
                 "hardware_summary": _hw_summary(w.hardware)}
                for w in workers
            ],
        }


def _hw_summary(hw) -> str:
    if not isinstance(hw, dict):
        return "Unknown"
    parts = []
    ram = hw.get("ram_mb", 0)
    if ram:
        parts.append(f"{ram // 1024}GB RAM")
    gpu = hw.get("gpu", {})
    if gpu.get("type") not in (None, "none", ""):
        vram = gpu.get("vram_mb", 0)
        if vram:
            parts.append(f"{gpu.get('model', gpu['type'])} {vram // 1024}GB")
        else:
            parts.append(gpu.get("model", gpu["type"]))
    npu = hw.get("npu", {})
    if npu.get("type", "none") != "none":
        parts.append(f"{npu['type']} {npu.get('tops', 0)} TOPS")
    return " · ".join(parts) if parts else "CPU only"
