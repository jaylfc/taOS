"""Job Worker — pulls and executes memory pipeline jobs (taOSmd).

Runs on each device (controller or worker) and processes jobs from the
queue based on available resources. The worker only pulls jobs whose
resource_type matches what this device can provide.

Usage:
    worker = JobWorker(queue, resource_types=["cpu", "npu"])
    await worker.run_once()  # Process one job
    await worker.run_loop()  # Process jobs continuously
"""

from __future__ import annotations

import json
import logging
import time

from taosmd.session_catalog import SessionCatalog

from .job_queue import (
    JobQueue, JOB_EMBED, JOB_EXTRACT, JOB_ENRICH, JOB_CRYSTALLIZE,
    JOB_SPLIT, JOB_INDEX,
)

logger = logging.getLogger(__name__)


class JobWorker:
    """Pulls jobs from the queue and executes them."""

    def __init__(
        self,
        queue: JobQueue,
        resource_types: list[str] | None = None,
        llm_url: str = "http://localhost:11434",
    ):
        self._queue = queue
        self._resource_types = resource_types  # None = accept all
        self._llm_url = llm_url
        self._running = False

    async def run_once(self) -> dict | None:
        """Pull and execute one job. Returns result or None if no jobs."""
        job = await self._queue.dequeue(resource_types=self._resource_types)
        if not job:
            return None

        job_id = job["id"]
        job_type = job["job_type"]
        payload = json.loads(job.get("payload_json", "{}"))

        logger.info("Processing job %s (%s)", job_id, job_type)
        t0 = time.time()

        try:
            result = await self._execute(job_type, payload)
            elapsed = time.time() - t0
            result["elapsed"] = round(elapsed, 2)
            await self._queue.complete(job_id, result)
            logger.info("Job %s completed in %.1fs", job_id, elapsed)
            return result
        except Exception as e:
            await self._queue.fail(job_id, str(e))
            logger.error("Job %s failed: %s", job_id, e)
            return {"error": str(e), "job_id": job_id}

    async def run_loop(self, poll_interval: float = 1.0, max_idle: float = 0) -> int:
        """Process jobs continuously.

        Args:
            poll_interval: Seconds between queue checks when idle.
            max_idle: Stop after this many seconds idle (0 = run forever).

        Returns:
            Number of jobs processed.
        """
        self._running = True
        processed = 0
        idle_since = time.time()

        while self._running:
            result = await self.run_once()
            if result:
                processed += 1
                idle_since = time.time()
            else:
                # No jobs — wait before polling again
                import asyncio
                await asyncio.sleep(poll_interval)

                if max_idle > 0 and (time.time() - idle_since) > max_idle:
                    logger.info("Worker idle for %.0fs, stopping", max_idle)
                    break

        return processed

    def stop(self):
        """Signal the run loop to stop after the current job."""
        self._running = False

    async def _execute(self, job_type: str, payload: dict) -> dict:
        """Execute a job based on its type."""
        if job_type == JOB_ENRICH:
            return await self._do_enrich(payload)
        elif job_type == JOB_CRYSTALLIZE:
            return await self._do_crystallize(payload)
        elif job_type == JOB_SPLIT:
            return await self._do_split(payload)
        elif job_type == JOB_INDEX:
            return await self._do_index(payload)
        else:
            return {"status": "unknown_job_type", "type": job_type}

    async def _do_enrich(self, payload: dict) -> dict:
        """Execute an enrichment job."""
        session_id = payload["session_id"]
        agent_name = payload.get("agent_name")
        model = payload.get("model", "qwen3:4b")
        tier = payload.get("tier", 2)
        llm_url = payload.get("llm_url", self._llm_url)
        catalog_db = payload.get("catalog_db", "data/session-catalog.db")

        catalog = SessionCatalog(db_path=catalog_db)
        await catalog.init()
        try:
            result = await catalog.enrich_session(
                session_id=session_id,
                llm_url=llm_url,
                model=model,
                tier=tier,
                agent_name=agent_name,
            )
            return {"status": "enriched", "session_id": session_id, **result}
        finally:
            await catalog.close()

    async def _do_crystallize(self, payload: dict) -> dict:
        """Execute a crystallization job."""
        from taosmd.session_catalog import SessionCatalog
        from taosmd.crystallize import CrystalStore
        from taosmd.knowledge_graph import TemporalKnowledgeGraph

        session_id = payload["session_id"]
        model = payload.get("model", "qwen3:4b")
        llm_url = payload.get("llm_url", self._llm_url)

        catalog = SessionCatalog(db_path=payload.get("catalog_db", "data/session-catalog.db"))
        cs = CrystalStore(db_path=payload.get("crystals_db", "data/crystals.db"))
        kg = TemporalKnowledgeGraph(db_path=payload.get("kg_db", "data/knowledge-graph.db"))

        await catalog.init()
        await cs.init()
        await kg.init()

        try:
            ctx = await catalog.get_session_context(session_id)
            if not ctx or not ctx.get("content_lines"):
                return {"status": "skipped", "reason": "no content"}

            turns = []
            for line in ctx["content_lines"]:
                try:
                    event = json.loads(line)
                    summary = event.get("summary") or (event.get("data", {}) or {}).get("content", "")
                    if summary:
                        turns.append({
                            "role": "user",
                            "content": summary,
                            "timestamp": event.get("timestamp", 0),
                        })
                except (json.JSONDecodeError, TypeError):
                    continue

            if not turns:
                return {"status": "skipped", "reason": "no turns"}

            crystal = await cs.crystallize(
                session_id=str(session_id),
                turns=turns,
                llm_url=llm_url,
                model=model,
                kg=kg,
            )
            return {"status": "crystallized", "session_id": session_id, "crystal_id": crystal.get("id")}
        finally:
            await catalog.close()
            await cs.close()
            await kg.close()

    async def _do_split(self, payload: dict) -> dict:
        """Execute a split job."""
        from taosmd.session_catalog import SessionCatalog

        date = payload["date"]
        catalog = SessionCatalog(
            db_path=payload.get("catalog_db", "data/session-catalog.db"),
            archive_dir=payload.get("archive_dir", "data/archive"),
            sessions_dir=payload.get("sessions_dir", "data/sessions"),
        )
        await catalog.init()
        try:
            result = catalog.split_day(date, force=payload.get("force", False))
            return {"status": "split", **result}
        finally:
            await catalog.close()

    async def _do_index(self, payload: dict) -> dict:
        """Execute a full index_day job (all 3 stages inline)."""
        from taosmd.catalog_pipeline import CatalogPipeline

        date = payload["date"]
        pipeline = CatalogPipeline(
            archive_dir=payload.get("archive_dir", "data/archive"),
            sessions_dir=payload.get("sessions_dir", "data/sessions"),
            catalog_db=payload.get("catalog_db", "data/session-catalog.db"),
            crystals_db=payload.get("crystals_db", "data/crystals.db"),
            kg_db=payload.get("kg_db", "data/knowledge-graph.db"),
            llm_url=payload.get("llm_url", self._llm_url),
            # No queue — inline execution to avoid recursive queuing
        )
        await pipeline.init()
        try:
            return await pipeline.index_day(date, force=payload.get("force", False))
        finally:
            await pipeline.close()
