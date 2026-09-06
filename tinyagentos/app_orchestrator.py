"""App container orchestrator — decides where to run apps and manages container lifecycle."""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class AppOrchestrator:
    def __init__(self, cluster_manager, streaming_store, http_client):
        self.cluster = cluster_manager
        self.sessions = streaming_store
        self.http_client = http_client

    def pick_worker(self, app_manifest: dict) -> str:
        """Pick the best worker for an app based on requirements.
        Returns worker name ('local' if no suitable remote worker)."""
        requires = app_manifest.get("requires", {})
        gpu_recommended = requires.get("gpu_recommended", False)

        if gpu_recommended:
            # Try to find an online worker with a GPU
            workers = self.cluster.get_workers()
            gpu_workers = [
                w for w in workers
                if w.status == "online"
                and isinstance(w.hardware, dict)
                and w.hardware.get("gpu", {}).get("type") not in (None, "none", "")
            ]
            if gpu_workers:
                # Pick the one with most VRAM
                gpu_workers.sort(
                    key=lambda w: w.hardware.get("gpu", {}).get("vram_mb", 0),
                    reverse=True,
                )
                return gpu_workers[0].name

        return "local"

    async def launch(self, app_id: str, app_manifest: dict, agent_name: str,
                     agent_type: str = "app-expert") -> dict:
        """Launch a streaming app container. Returns session info."""
        worker_name = self.pick_worker(app_manifest)

        session_id = await self.sessions.create_session(
            app_id=app_id,
            agent_name=agent_name,
            agent_type=agent_type,
            worker_name=worker_name,
            container_id="pending",
        )

        # In production, this would:
        # 1. Check if worker has the image cached
        # 2. If not, tell worker to pull from controller's OCI registry
        # 3. Start the container with GPU passthrough + workspace mounts
        # 4. Wait for KasmVNC + agent-bridge to be ready
        # 5. Update session with container_id and status

        # For now, mark as "running" with placeholder container ID
        await self.sessions.update_status(session_id, "running")

        logger.info(f"Launched {app_id} on {worker_name} (session {session_id})")
        return {
            "session_id": session_id,
            "app_id": app_id,
            "worker_name": worker_name,
            "status": "running",
        }

    async def stop(self, session_id: str) -> dict:
        """Stop a streaming app session and clean up the container."""
        session = await self.sessions.get_session(session_id)
        if not session:
            return {"error": "Session not found"}

        # In production: docker stop/rm on the worker
        await self.sessions.update_status(session_id, "stopped")
        logger.info(f"Stopped session {session_id}")
        return {"session_id": session_id, "status": "stopped"}

    async def get_bridge_url(self, session_id: str) -> str | None:
        """Get the agent-bridge URL for a session."""
        session = await self.sessions.get_session(session_id)
        if not session or session["status"] != "running":
            return None
        # In production: resolve worker IP + bridge port
        # For now: assume local
        return "http://localhost:9100"
