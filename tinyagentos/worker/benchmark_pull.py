"""Worker-side pickup for controller-queued benchmark runs.

Why this exists: the worker agent is a poller (register + heartbeat) and
exposes no inbound HTTP surface, so the controller cannot POST "run the
benchmark" to it. Instead a "Re-run benchmarks" click is *queued* on the
controller and handed over in the next heartbeat response, and this module
turns that delivery into a run.

Deliberate choices:

- **One run at a time.** The controller re-delivers a queued request on
  every heartbeat until results arrive, so a delivery that arrives while a
  run is in flight is ignored. That re-delivery is also what retries a run
  that died (worker restart mid-suite) -- no separate retry timer.
- **A short grace window after a run ends.** Results land a moment after the
  run exits, so a heartbeat in that gap would otherwise start a second
  one-to-two-minute suite for nothing. Re-delivery is ignored for
  ``retry_grace_seconds`` after the run exits; a run that never manages to post
  its results is retried after the window.
- **Bounded attempts.** After ``max_attempts`` deliveries for the same
  ``requested_at`` this stops starting runs, so a permanently broken worker
  cannot loop on one click forever. A *new* click carries a new
  ``requested_at`` and resets the count.
- **Subprocess, not in-process.** The run is
  ``python -m tinyagentos.benchmark.runner`` -- the exact code path the
  install-worker.sh first-attach hook uses, so the two can never drift, and
  a crashing suite cannot take the worker daemon down with it.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_GRACE_SECONDS = 30.0


class BenchmarkRequestRunner:
    """Starts at most one queued benchmark run at a time.

    ``spawn`` is injectable (tests pass a fake); by default the run is a
    real subprocess whose stdout/stderr are inherited, so it lands in the
    worker's journal alongside the daemon's own logging.
    """

    def __init__(
        self,
        *,
        controller_url: str,
        worker_name: str,
        python_executable: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_grace_seconds: float = DEFAULT_RETRY_GRACE_SECONDS,
        spawn: Optional[Callable[[list[str]], Awaitable[Any]]] = None,
    ):
        self.controller_url = controller_url.rstrip("/")
        self.worker_name = worker_name
        self.python_executable = python_executable or sys.executable
        self.max_attempts = max_attempts
        self.retry_grace_seconds = retry_grace_seconds
        self._spawn = spawn
        self._process: Any = None
        self._requested_at: float | None = None
        self._attempts = 0
        self._exited_at: float | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def attempts(self) -> int:
        return self._attempts

    def argv(self, request_id: float) -> list[str]:
        """The runner command. Manual runs are never first-join runs.

        ``--request-id`` echoes the queue entry's ``requested_at`` back in the
        report, so the controller clears exactly the run this invocation served
        (a click that arrived meanwhile keeps its place in the queue).
        """
        return [
            self.python_executable,
            "-m",
            "tinyagentos.benchmark.runner",
            "--report-to",
            self.controller_url,
            "--worker-name",
            self.worker_name,
            "--request-id",
            str(request_id),
        ]

    @staticmethod
    def request_id_of(request: Optional[dict]) -> Optional[float]:
        """The queued run's timestamp, or None when the delivery is unusable.

        The controller always sends a float. Anything else means a malformed or
        hostile heartbeat response, and starting a run for it would burn the
        worker while corrupting the attempt bookkeeping (a float is what marks
        which request the attempts belong to).
        """
        if not request:
            return None
        requested_at = request.get("requested_at")
        if isinstance(requested_at, bool) or not isinstance(requested_at, (int, float)):
            return None
        return float(requested_at)

    def should_start(self, request: Optional[dict]) -> bool:
        """True when this delivery should start a run.

        False for: no request, malformed request (no worker id, or a
        ``requested_at`` that is not a number), a run already in flight, a
        re-delivery inside the post-run grace window (its results are probably
        still in flight), or the attempt budget for this ``requested_at``
        already spent.
        """
        if not request or not request.get("worker_id"):
            return False
        requested_at = self.request_id_of(request)
        if requested_at is None:
            return False
        if self.running:
            return False
        if requested_at == self._requested_at:
            if self._attempts >= self.max_attempts:
                return False
            if (
                self._exited_at is not None
                and (time.monotonic() - self._exited_at) < self.retry_grace_seconds
            ):
                return False
        return True

    async def handle(self, request: Optional[dict]) -> bool:
        """Act on a heartbeat-delivered request. True when a run started."""
        if not request:
            # Nothing queued: the last run's results landed. Drop the
            # bookkeeping so the next click counts as fresh.
            self._requested_at = None
            self._attempts = 0
            self._exited_at = None
            return False

        requested_at = self.request_id_of(request)
        if not request.get("worker_id") or requested_at is None:
            logger.warning("benchmark pickup: ignoring malformed request %r", request)
            return False

        if self._process is not None and self._process.returncode is not None:
            # The run ended; its results POST may still be in flight.
            if self._exited_at is None:
                self._exited_at = time.monotonic()

        if not self.should_start(request):
            return False

        if requested_at == self._requested_at:
            self._attempts += 1
        else:
            self._requested_at = requested_at
            self._attempts = 1

        argv = self.argv(requested_at)
        try:
            self._process = await self._spawn_process(argv)
        except Exception:  # noqa: BLE001 -- the heartbeat loop must survive this
            logger.exception("benchmark pickup: could not start %s", argv)
            self._process = None
            return False

        self._exited_at = None
        logger.info(
            "benchmark pickup: started manual run for '%s' (attempt %d/%d)",
            self.worker_name,
            self._attempts,
            self.max_attempts,
        )
        return True

    async def _spawn_process(self, argv: list[str]) -> Any:
        if self._spawn is not None:
            return await self._spawn(argv)
        return await asyncio.create_subprocess_exec(*argv)

    async def wait(self) -> int:
        """Wait for the in-flight run to exit; returns its exit code."""
        process = self._process
        if process is None:
            return 0
        if process.returncode is None:
            await process.wait()
        code = process.returncode
        return int(code) if code is not None else 0
