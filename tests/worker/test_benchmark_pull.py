"""Worker-side pickup of controller-queued benchmark runs."""
from __future__ import annotations

import asyncio
import secrets
import sys

import pytest

from tinyagentos.worker.agent import WorkerAgent
from tinyagentos.worker.benchmark_pull import BenchmarkRequestRunner
from tinyagentos.worker.pairing import save_signing_key


REQUEST = {"worker_id": "pi4", "requested_at": 1_700_000_000.0, "force": False}


class _FakeProcess:
    """Stands in for the benchmark runner subprocess."""

    def __init__(self, returncode: int | None = None):
        self.returncode = returncode
        self.waited = False

    async def wait(self) -> int:
        self.waited = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _runner(spawn, **kwargs) -> BenchmarkRequestRunner:
    return BenchmarkRequestRunner(
        controller_url="http://controller:6969/",
        worker_name="pi4",
        spawn=spawn,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_starts_the_runner_subprocess_with_the_controller_url():
    spawned: list[list[str]] = []

    async def spawn(argv):
        spawned.append(argv)
        return _FakeProcess()

    runner = _runner(spawn)
    assert await runner.handle(REQUEST) is True

    assert spawned == [
        [
            sys.executable,
            "-m",
            "tinyagentos.benchmark.runner",
            "--report-to",
            "http://controller:6969",
            "--worker-name",
            "pi4",
            "--request-id",
            str(REQUEST["requested_at"]),
        ]
    ]
    # Manual runs are never first-join runs.
    assert "--first-join" not in spawned[0]


@pytest.mark.asyncio
async def test_one_run_at_a_time_and_bounded_attempts():
    spawned: list[list[str]] = []

    async def spawn(argv):
        spawned.append(argv)
        return _FakeProcess()

    runner = _runner(spawn, max_attempts=2, retry_grace_seconds=0)

    assert await runner.handle(REQUEST) is True
    # The controller re-delivers on every heartbeat; a delivery while a run is
    # in flight must not start a second one.
    assert await runner.handle(REQUEST) is False
    assert len(spawned) == 1

    # The run exited without the queue being cleared (worker restarted, results
    # POST failed): re-delivery retries once...
    runner._process.returncode = 1
    assert await runner.handle(REQUEST) is True
    # ...then gives up, so one click cannot loop forever on a broken worker.
    runner._process.returncode = 1
    assert await runner.handle(REQUEST) is False
    assert len(spawned) == 2
    assert runner.attempts == 2

    # A fresh click carries a new requested_at and gets a fresh budget.
    assert await runner.handle({**REQUEST, "requested_at": 1_700_000_999.0}) is True
    assert len(spawned) == 3


@pytest.mark.asyncio
async def test_no_request_and_malformed_request_are_noops():
    spawned: list[list[str]] = []

    async def spawn(argv):
        spawned.append(argv)
        return _FakeProcess()

    runner = _runner(spawn)
    assert await runner.handle(None) is False
    assert await runner.handle({}) is False
    assert await runner.handle({"requested_at": 1.0}) is False
    # A heartbeat response with a worker id but no usable requested_at must not
    # start a run (it would corrupt the attempt bookkeeping too).
    assert await runner.handle({"worker_id": "pi4"}) is False
    assert await runner.handle({"worker_id": "pi4", "requested_at": "soon"}) is False
    assert await runner.handle({"worker_id": "pi4", "requested_at": None}) is False
    assert await runner.handle({"worker_id": "pi4", "requested_at": True}) is False
    assert spawned == []
    assert runner.attempts == 0


@pytest.mark.asyncio
async def test_drained_queue_resets_attempt_bookkeeping():
    async def spawn(argv):
        return _FakeProcess()

    runner = _runner(spawn, max_attempts=1, retry_grace_seconds=0)
    assert await runner.handle(REQUEST) is True
    runner._process.returncode = 1
    assert await runner.handle(REQUEST) is False  # budget spent
    assert runner.attempts == 1

    # Results landed and the controller cleared the queue.
    assert await runner.handle(None) is False
    assert runner.attempts == 0


@pytest.mark.asyncio
async def test_a_finished_run_is_not_restarted_before_its_results_land():
    """The gap between the run exiting and its results POST landing must not
    start a second one-to-two-minute suite."""
    spawned: list[list[str]] = []

    async def spawn(argv):
        spawned.append(argv)
        return _FakeProcess()

    runner = _runner(spawn, retry_grace_seconds=0.05)
    assert await runner.handle(REQUEST) is True

    runner._process.returncode = 0  # suite finished, results POST in flight
    assert await runner.handle(REQUEST) is False
    assert len(spawned) == 1

    # A run that never managed to post its results IS retried, once the
    # window has passed.
    await asyncio.sleep(0.06)
    assert await runner.handle(REQUEST) is True
    assert len(spawned) == 2


@pytest.mark.asyncio
async def test_spawn_failure_is_not_fatal():
    """A missing interpreter must not kill the worker loop."""
    attempts: list[int] = []

    async def spawn(argv):
        attempts.append(1)
        if len(attempts) == 1:
            raise FileNotFoundError("python missing")
        return _FakeProcess()

    runner = _runner(spawn)
    assert await runner.handle(REQUEST) is False
    # The next delivery tries again rather than giving up permanently.
    assert await runner.handle(REQUEST) is True


@pytest.mark.asyncio
async def test_agent_starts_a_run_from_the_heartbeat_delivery(tmp_path):
    save_signing_key(tmp_path, secrets.token_bytes(32))
    agent = WorkerAgent("http://controller:6969", name="pi4", state_dir=tmp_path)

    spawned: list[list[str]] = []

    async def spawn(argv):
        spawned.append(argv)
        return _FakeProcess()

    agent._benchmark_pull = _runner(spawn)

    # Nothing delivered yet: the run loop's hook is a no-op.
    assert await agent._maybe_start_requested_benchmark() is False
    assert spawned == []

    agent._last_heartbeat_request = REQUEST
    assert await agent._maybe_start_requested_benchmark() is True
    assert "pi4" in spawned[0]
    # The queue entry's id is echoed so the controller clears exactly this run.
    assert spawned[0][-1] == str(REQUEST["requested_at"])
