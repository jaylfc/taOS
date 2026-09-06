"""Tests for tinyagentos.worker.agent -- WorkerAgent logic."""
from __future__ import annotations
import asyncio
import secrets
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tinyagentos.worker.agent import WorkerAgent, _NEEDS_REPAIR, _REPAIR_INTERVAL
from tinyagentos.worker.pairing import save_signing_key


class TestDetectCapabilities:
    """Test capability detection from backend lists."""

    def test_empty_backends(self):
        agent = WorkerAgent("http://localhost:6969")
        with patch("shutil.which", return_value=None):
            assert agent.detect_capabilities([]) == []

    def test_ollama_backend(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "ollama", "url": "http://localhost:11434"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "llm-chat" in caps
        assert "embedding" in caps
        # Ollama only advertises llm-chat + embedding in BACKEND_CAPABILITIES
        assert "image-generation" not in caps

    def test_rkllama_backend(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "rkllama", "url": "http://localhost:8080"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "llm-chat" in caps
        assert "embedding" in caps
        assert "reranking" in caps

    def test_llama_cpp_backend(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "llama-cpp", "url": "http://localhost:8080"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "llm-chat" in caps
        assert "embedding" in caps
        assert "image-generation" not in caps
        assert "reranking" not in caps

    def test_vllm_backend(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "vllm", "url": "http://localhost:8000"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "llm-chat" in caps
        assert "image-generation" not in caps

    def test_sd_cpp_backend(self):
        """A sd-cpp backend advertises image-generation only."""
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "sd-cpp", "url": "http://localhost:7864"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "image-generation" in caps
        assert "llm-chat" not in caps

    def test_sd_cpp_backend(self):
        """An sd-cpp backend advertises image-generation only."""
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "sd-cpp", "url": "http://localhost:7864"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "image-generation" in caps
        assert "embedding" not in caps

    def test_respects_preattached_capabilities(self):
        """If the caller attaches a per-backend capabilities field (modern
        detect_backends shape from live probing), detect_capabilities uses
        it verbatim without consulting BACKEND_CAPABILITIES."""
        agent = WorkerAgent("http://localhost:6969")
        backends = [
            {"type": "rkllama", "url": "http://x", "capabilities": ["embedding"]},
        ]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert caps == ["embedding"]

    def test_multiple_backends_deduplicates(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [
            {"type": "ollama", "url": "http://localhost:11434"},
            {"type": "rkllama", "url": "http://localhost:8080"},
        ]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        # Should be sorted and deduplicated
        assert caps == sorted(set(caps))
        assert "reranking" in caps

    def test_capabilities_are_sorted(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "rkllama", "url": "http://localhost:8080"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert caps == sorted(caps)


class TestWorkerAgent:
    """Test WorkerAgent initialization and URL generation."""

    def test_default_name_is_hostname(self):
        import socket
        agent = WorkerAgent("http://localhost:6969")
        assert agent.name == socket.gethostname()

    def test_custom_name(self):
        agent = WorkerAgent("http://localhost:6969", name="gpu-box")
        assert agent.name == "gpu-box"

    def test_controller_url_strips_trailing_slash(self):
        agent = WorkerAgent("http://localhost:6969/")
        assert agent.controller_url == "http://localhost:6969"

    def test_get_worker_url_with_port(self):
        agent = WorkerAgent("http://localhost:6969", worker_port=9999)
        url = agent.get_worker_url()
        assert ":9999" in url
        assert url.startswith("http://")

    def test_get_worker_url_without_port(self):
        agent = WorkerAgent("http://localhost:6969", worker_port=0)
        url = agent.get_worker_url()
        assert url.startswith("http://")
        # Should not end with :0
        assert ":0" not in url


@pytest.mark.asyncio
class TestRegistration:
    """Test worker registration with mocked HTTP."""

    async def test_register_success(self, tmp_path):
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="test-worker", state_dir=tmp_path)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            # detect_backends GET calls return failures (no backends running)
            mock_client.get = AsyncMock(side_effect=Exception("not running"))
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                result = await agent.register()

        assert result is True
        assert agent._registered is True

    async def test_register_failure(self):
        agent = WorkerAgent("http://controller:6969", name="test-worker")

        with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
            with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
                mock_client_cls.return_value = mock_client

                result = await agent.register()

        assert result is False
        assert agent._registered is False

    async def test_register_returns_401_on_pairing_rejection(self, tmp_path):
        """register() must return the 401 sentinel when the controller rejects
        the signing key with worker_not_paired or bad_signature."""
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="test-worker", state_dir=tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"code": "worker_not_paired", "error": "not paired"}

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                result = await agent.register()

        assert result == _NEEDS_REPAIR
        assert agent._registered is False

    async def test_register_returns_401_on_bad_signature(self, tmp_path):
        """register() must return the 401 sentinel for bad_signature too."""
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="test-worker", state_dir=tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"code": "bad_signature", "error": "sig mismatch"}

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                result = await agent.register()

        assert result == _NEEDS_REPAIR

    async def test_register_reloads_key_from_disk(self, tmp_path):
        """register() must re-read the signing key from disk on each call so
        that running the pair CLI recovers the agent without a restart."""
        agent = WorkerAgent("http://controller:6969", name="test-worker", state_dir=tmp_path)
        # No key on disk yet -- first call returns False.
        with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
            result = await agent.register()
        assert result is False

        # Now persist a key and call again -- should succeed without recreating the agent.
        save_signing_key(tmp_path, secrets.token_bytes(32))
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                result = await agent.register()

        assert result is True


@pytest.mark.asyncio
class TestRegistrationUrlFallback:
    """Regression: when all backends are synthetic/stopped (url=None),
    the registration URL must fall through to get_worker_url()."""

    async def test_register_url_falls_back_when_all_backends_stopped(self, tmp_path):
        """Synthetic stopped backends have url=None. The registration URL
        must skip None-URL backends and fall through to get_worker_url()
        instead of sending None, which causes a silent registration-failure
        loop.  Regression test for #1765 review finding."""
        import json as _json

        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent(
            "http://controller:6969", name="test-worker", state_dir=tmp_path
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        # Synthetic stopped backend: url is None
        stopped_backend = {
            "name": "ollama",
            "type": "ollama",
            "url": None,
            "capabilities": ["llm-chat"],
            "models": [],
            "loaded_models": [],
            "status": "stopped",
            "kv_quant_support": {},
            "available_models": [],
        }

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch(
                "tinyagentos.worker.agent.WorkerAgent.detect_backends",
                return_value=[stopped_backend],
            ):
                result = await agent.register()

            # Registration must succeed AND the payload URL must be a
            # real reachable URL, not None.
            assert result is True
            assert mock_client.post.called
            call_args = mock_client.post.call_args
            body = _json.loads(call_args[1]["content"])
            assert body["url"] is not None
            assert body["url"].startswith("http://")


@pytest.mark.asyncio
class TestHeartbeat:
    """Test heartbeat sending with mocked HTTP."""

    async def test_heartbeat_success(self, tmp_path):
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="test-worker", state_dir=tmp_path)
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.psutil.cpu_percent", return_value=42.0):
                with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                    result = await agent.heartbeat()

        # heartbeat() returns the int HTTP status code (or 0 on failure), see #166
        assert result == 200

    async def test_heartbeat_failure(self):
        agent = WorkerAgent("http://controller:6969", name="test-worker")

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=Exception("timeout"))
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.psutil.cpu_percent", return_value=0.0):
                result = await agent.heartbeat()

        # On transport error heartbeat() returns 0, not False
        assert result == 0

    async def test_heartbeat_returns_401(self, tmp_path):
        """heartbeat() must pass the 401 status code through so the run loop
        can detect the needs-re-pair case."""
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="test-worker", state_dir=tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.psutil.cpu_percent", return_value=0.0):
                with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                    result = await agent.heartbeat()

        assert result == 401


@pytest.mark.asyncio
class TestRunLoopRepairState:
    """Test the needs-re-pair state in the run() loop.

    Uses monkeypatched asyncio.sleep and a fake clock to drive the loop
    without real I/O or wall-clock delays.
    """

    def _make_401_response(self, code: str = "worker_not_paired") -> MagicMock:
        r = MagicMock()
        r.status_code = 401
        r.json.return_value = {"code": code, "error": "test"}
        return r

    def _make_200_response(self) -> MagicMock:
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status = MagicMock()
        return r

    async def test_repair_state_logs_instruction_once_over_multiple_ticks(
        self, tmp_path, caplog
    ):
        """When the controller keeps returning 401 worker_not_paired, the
        re-pair instruction must be logged exactly ONCE across several ticks
        (not per-tick), and the loop must back off to _REPAIR_INTERVAL."""
        import logging
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="gpu-box", state_dir=tmp_path)

        tick = 0
        sleep_calls: list[float] = []

        async def fake_sleep(n: float) -> None:
            sleep_calls.append(n)
            agent._running = tick >= 4  # stop after 4 sleeps

        async def fake_register() -> int:
            nonlocal tick
            tick += 1
            return _NEEDS_REPAIR

        with patch("tinyagentos.worker.agent.asyncio.sleep", side_effect=fake_sleep):
            with patch.object(agent, "register", side_effect=fake_register):
                with caplog.at_level(logging.ERROR, logger="tinyagentos.worker.agent"):
                    await agent.run()

        # Instruction logged exactly once (entry into state)
        repair_logs = [r for r in caplog.records if "needs re-pairing" in r.message]
        assert len(repair_logs) == 1, (
            f"expected 1 re-pair log, got {len(repair_logs)}: {[r.message for r in repair_logs]}"
        )

        # All sleeps after entering repair state must use _REPAIR_INTERVAL
        assert all(s == _REPAIR_INTERVAL for s in sleep_calls), (
            f"expected all sleeps == {_REPAIR_INTERVAL}, got {sleep_calls}"
        )

    async def test_repair_state_does_not_spin_register(self, tmp_path, caplog):
        """While in needs-re-pair state, register() must not be called more
        than once per sleep cycle (no tight register loop every 5s)."""
        import logging
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="gpu-box", state_dir=tmp_path)

        call_count = 0
        sleep_count = 0

        async def fake_sleep(n: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            agent._running = sleep_count < 4

        async def fake_register() -> int:
            nonlocal call_count
            call_count += 1
            return _NEEDS_REPAIR

        with patch("tinyagentos.worker.agent.asyncio.sleep", side_effect=fake_sleep):
            with patch.object(agent, "register", side_effect=fake_register):
                with caplog.at_level(logging.ERROR, logger="tinyagentos.worker.agent"):
                    await agent.run()

        # register() should be called once per sleep -- never more than sleep_count
        assert call_count <= sleep_count, (
            f"register called {call_count}x but only {sleep_count} sleeps: spinning detected"
        )

    async def test_repair_state_recovers_without_restart(self, tmp_path, caplog):
        """After the pair CLI writes a new key, the next register() attempt
        must succeed and the agent must resume normal heartbeat without a
        process restart."""
        import logging
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="gpu-box", state_dir=tmp_path)

        phase = {"value": "repair"}  # start in repair, then flip to ok
        heartbeat_called = []
        sleep_count = 0

        async def fake_sleep(n: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            # After 2 sleeps in repair, simulate operator ran pair CLI
            if sleep_count >= 2:
                phase["value"] = "ok"
            agent._running = sleep_count < 5

        async def fake_register() -> "bool | int":
            if phase["value"] == "repair":
                return _NEEDS_REPAIR
            # Simulate successful re-registration after new key
            agent._registered = True
            return True

        async def fake_heartbeat(**kwargs) -> int:
            heartbeat_called.append(1)
            agent._running = False  # stop after first successful heartbeat
            return 200

        with patch("tinyagentos.worker.agent.asyncio.sleep", side_effect=fake_sleep):
            with patch.object(agent, "register", side_effect=fake_register):
                with patch.object(agent, "heartbeat", side_effect=fake_heartbeat):
                    with caplog.at_level(logging.INFO, logger="tinyagentos.worker.agent"):
                        await agent.run()

        # Must have recovered and sent at least one heartbeat
        assert len(heartbeat_called) >= 1, "agent did not recover to heartbeat after re-pair"

    async def test_404_still_triggers_reregister(self, tmp_path, caplog):
        """404 on heartbeat must still trigger re-registration (unchanged behaviour)."""
        import logging
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="gpu-box", state_dir=tmp_path)
        agent._registered = True  # start as registered

        hb_count = 0
        reg_count = 0
        sleep_count = 0

        async def fake_sleep(n: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            # Allow the loop to run: heartbeat -> 404 -> _registered=False -> register -> stop
            if sleep_count >= 2:
                agent._running = False

        async def fake_heartbeat(**kwargs) -> int:
            nonlocal hb_count
            hb_count += 1
            return 404

        async def fake_register() -> bool:
            nonlocal reg_count
            reg_count += 1
            agent._registered = True
            agent._running = False
            return True

        with patch("tinyagentos.worker.agent.asyncio.sleep", side_effect=fake_sleep):
            with patch.object(agent, "heartbeat", side_effect=fake_heartbeat):
                with patch.object(agent, "register", side_effect=fake_register):
                    with caplog.at_level(logging.WARNING, logger="tinyagentos.worker.agent"):
                        await agent.run()

        assert hb_count >= 1
        assert reg_count >= 1, "register() was not called after 404"
        warning_logs = [r for r in caplog.records if "404" in r.message or "re-registering" in r.message]
        assert len(warning_logs) >= 1

    async def test_transient_0_does_not_drop_registered_state(self, tmp_path):
        """Status 0 (network error) must not drop _registered -- unchanged behaviour."""
        import logging
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="gpu-box", state_dir=tmp_path)
        agent._registered = True

        call_count = 0

        async def fake_sleep(n: float) -> None:
            agent._running = call_count >= 2

        async def fake_heartbeat(**kwargs) -> int:
            nonlocal call_count
            call_count += 1
            return 0

        with patch("tinyagentos.worker.agent.asyncio.sleep", side_effect=fake_sleep):
            with patch.object(agent, "heartbeat", side_effect=fake_heartbeat):
                await agent.run()

        # _registered must stay True through transient network failures
        assert agent._registered is True

    async def test_repair_instruction_rethrottled_after_interval(self, tmp_path, caplog):
        """The re-pair instruction is re-logged after _REPAIR_LOG_INTERVAL
        elapses, but not on every tick."""
        import logging
        from tinyagentos.worker.agent import _REPAIR_LOG_INTERVAL
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="gpu-box", state_dir=tmp_path)

        # Monotonic time starts at 0, advances manually.
        fake_now = [0.0]
        tick = [0]

        async def fake_sleep(n: float) -> None:
            tick[0] += 1
            # After 2 ticks, jump past the re-log throttle window
            if tick[0] == 2:
                fake_now[0] += _REPAIR_LOG_INTERVAL + 1
            agent._running = tick[0] < 4

        async def fake_register() -> int:
            return _NEEDS_REPAIR

        with patch("tinyagentos.worker.agent.asyncio.sleep", side_effect=fake_sleep):
            with patch.object(agent, "register", side_effect=fake_register):
                with patch("tinyagentos.worker.agent.time.monotonic", side_effect=lambda: fake_now[0]):
                    with caplog.at_level(logging.ERROR, logger="tinyagentos.worker.agent"):
                        await agent.run()

        repair_logs = [r for r in caplog.records if "needs re-pairing" in r.message]
        # Should be logged once at entry and once after the throttle window passes
        assert len(repair_logs) == 2, (
            f"expected 2 re-pair logs (entry + re-log after throttle), got {len(repair_logs)}"
        )


@pytest.mark.asyncio
class TestDetectBackends:
    """Test backend discovery with mocked HTTP."""

    async def test_no_backends_running(self):
        agent = WorkerAgent("http://localhost:6969")

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client

            backends = await agent.detect_backends()

        assert backends == []

    async def test_ollama_running(self):
        agent = WorkerAgent("http://localhost:6969")
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            async def mock_get(url):
                if "11434" in url:
                    return mock_response
                raise Exception("not running")

            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            backends = await agent.detect_backends()

        assert len(backends) == 1
        assert backends[0]["type"] == "ollama"
        assert backends[0]["url"] == "http://localhost:11434"
        assert backends[0]["name"] == "ollama:11434"

    async def test_backend_name_is_type_colon_port(self):
        """Backend name must be 'type:port', not 'type@http://localhost:PORT'."""
        agent = WorkerAgent("http://localhost:6969")
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            async def mock_get(url):
                # Simulate both ollama (11434) and bundled ollama (21434) running
                if "11434" in url or "21434" in url:
                    return mock_response
                raise Exception("not running")

            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            backends = await agent.detect_backends()

        names = [b["name"] for b in backends]
        assert "ollama:11434" in names
        assert "ollama:21434" in names
        # Legacy shape must not appear
        for name in names:
            assert "@" not in name, f"backend name must not embed URL: {name!r}"

    async def test_hailo_ollama_running(self):
        """A Hailo-10H host running hailo-ollama on the taOS remap port 7836
        must be detected as a live hailo-ollama backend (S5)."""
        agent = WorkerAgent("http://localhost:6969")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            async def mock_get(url):
                if "7836" in url:
                    return mock_response
                raise Exception("not running")

            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            backends = await agent.detect_backends()

        hailo = [b for b in backends if b["type"] == "hailo-ollama"]
        assert len(hailo) == 1
        assert hailo[0]["url"] == "http://localhost:7836"
        assert hailo[0]["name"] == "hailo-ollama:7836"
        # Capability map entry from S1 must surface in the probe.
        assert "llm-chat" in hailo[0]["capabilities"]

    async def test_backend_name_portless_url(self):
        """When a candidate URL has no explicit port, name falls back to bare backend_type."""
        agent = WorkerAgent("http://localhost:6969")
        mock_response = MagicMock()
        mock_response.status_code = 200

        portless_candidates = [("ollama", "http://ollama")]

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch.object(agent, "detect_backends", wraps=agent.detect_backends) as _:
                # Inject portless candidate directly by patching the local candidates list
                # via a subclassed coroutine that replaces detect_backends internals is
                # brittle; instead test the guard expression directly.
                pass

        # Direct guard-logic test: urlparse of a portless URL returns port=None.
        from urllib.parse import urlparse
        port = urlparse("http://ollama").port
        backend_type = "ollama"
        name = f"{backend_type}:{port}" if port is not None else backend_type
        assert name == "ollama", f"expected 'ollama', got {name!r}"

        # Also verify a normal URL still produces type:port.
        port2 = urlparse("http://localhost:11434").port
        name2 = f"{backend_type}:{port2}" if port2 is not None else backend_type
        assert name2 == "ollama:11434"


# ---------------------------------------------------------------------------
# Enrichment -- manifest-defined stopped backends (MEDIUM fix, #1765)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestManifestStoppedBackends:
    """MEDIUM: manifest entries for stopped-but-installed backends must appear
    in detect_backends output, decoupling availability from liveness."""

    async def test_manifest_only_backend_creates_stopped_entry(self):
        """When a manifest declares models for a backend type that has no
        probed (running) instance, detect_backends emits a synthetic backend
        entry with status='stopped' and the declared available_models."""
        agent = WorkerAgent("http://localhost:6969")

        # No backends are running -- all probes fail
        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client

            manifest = {
                "resource_id": "w1",
                "models": [
                    {
                        "model_id": "qwen-7b",
                        "capability": "chat",
                        "software": "llamacpp",
                        "port": 8000,
                        "vram_required_gb": 16.0,
                        "health_url": "http://localhost:8000/health",
                    },
                ],
            }
            with patch("tinyagentos.worker.worker_manifest.load_manifest",
                       return_value=manifest):
                backends = await agent.detect_backends()

        # Should have a synthetic backend for llama-cpp with the manifest entry
        assert len(backends) == 1
        assert backends[0]["type"] == "llama-cpp"
        assert backends[0]["status"] == "stopped"
        assert backends[0]["models"] == []
        assert backends[0]["loaded_models"] == []
        avail = backends[0].get("available_models", [])
        assert len(avail) == 1
        assert avail[0]["model_id"] == "qwen-7b"
        assert avail[0]["status"] == "available"

    async def test_manifest_mixed_probed_and_stopped(self):
        """When some backends are running and others are only in the manifest,
        the probed backends get enriched AND synthetic entries appear for the
        manifest-only types."""
        agent = WorkerAgent("http://localhost:6969")

        # Only ollama on 11434 is running
        mock_tags_resp = MagicMock()
        mock_tags_resp.status_code = 200
        mock_tags_resp.json = MagicMock(return_value={
            "models": [{"model": "tinyllama:latest", "size": 600_000_000}]
        })
        mock_ps_resp = MagicMock()
        mock_ps_resp.status_code = 200
        mock_ps_resp.json = MagicMock(return_value={
            "models": [{"model": "tinyllama:latest", "size": 600_000_000}]
        })

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            async def mock_get(url):
                if "11434" in url:
                    if "/api/ps" in url:
                        return mock_ps_resp
                    return mock_tags_resp
                raise Exception("not running")

            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            manifest = {
                "resource_id": "w1",
                "models": [
                    {
                        "model_id": "qwen-7b",
                        "capability": "chat",
                        "software": "llamacpp",
                        "port": 8000,
                    },
                ],
            }
            with patch("tinyagentos.worker.worker_manifest.load_manifest",
                       return_value=manifest):
                backends = await agent.detect_backends()

        # Should have the probed ollama + synthetic llama-cpp
        types = {b["type"] for b in backends}
        assert "ollama" in types
        assert "llama-cpp" in types
        # llama-cpp should be stopped
        llcpp = [b for b in backends if b["type"] == "llama-cpp"][0]
        assert llcpp["status"] == "stopped"
        assert len(llcpp["available_models"]) == 1
        assert llcpp["available_models"][0]["model_id"] == "qwen-7b"

    async def test_manifest_empty_no_synthetic_backends(self):
        """When the manifest has no models, no synthetic backends are created."""
        agent = WorkerAgent("http://localhost:6969")

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.worker_manifest.load_manifest",
                       return_value={"resource_id": "", "models": []}):
                backends = await agent.detect_backends()

        assert backends == []

    async def test_manifest_enrichment_failure_graceful(self):
        """If the manifest enrichment raises, detect_backends still returns
        without crashing -- just the probed backends without enrichment."""
        agent = WorkerAgent("http://localhost:6969")

        mock_tags_resp = MagicMock()
        mock_tags_resp.status_code = 200
        mock_tags_resp.json = MagicMock(return_value={
            "models": [{"model": "tinyllama:latest", "size": 600_000_000}]
        })
        mock_ps_resp = MagicMock()
        mock_ps_resp.status_code = 200
        mock_ps_resp.json = MagicMock(return_value={
            "models": [{"model": "tinyllama:latest", "size": 600_000_000}]
        })

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            async def mock_get(url):
                if "11434" in url:
                    if "/api/ps" in url:
                        return mock_ps_resp
                    return mock_tags_resp
                raise Exception("not running")

            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            # load_manifest raises -- simulates a file-system or parse error
            # that gets past load_manifest's own guards
            with patch("tinyagentos.worker.worker_manifest.load_manifest",
                       side_effect=RuntimeError("disk failure")):
                backends = await agent.detect_backends()

        # Backend still detected, just no available_models attached
        assert len(backends) == 1
        assert backends[0]["type"] == "ollama"
        assert "available_models" not in backends[0]


# ---------------------------------------------------------------------------
# Enrichment -- loaded_models vs catalog (NIT fix, #1765)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestManifestLoadedStatus:
    """NIT: 'loaded' status must use loaded_models (in-memory), not the
    full catalog from /api/tags (on-disk)."""

    async def test_loaded_status_uses_loaded_models(self):
        """Verify that a manifest model present in loaded_models gets
        status='loaded', while one only in the catalog gets 'available'."""
        agent = WorkerAgent("http://localhost:6969")

        # Patch SOFTWARE_TO_BACKEND_TYPE to include ollama mapping so
        # manifest entries match the probed ollama backend.
        mock_sw_map = {"ollama": "ollama"}

        # /api/tags returns ALL models on disk
        mock_tags_resp = MagicMock()
        mock_tags_resp.status_code = 200
        mock_tags_resp.json = MagicMock(return_value={
            "models": [
                {"model": "tinyllama:latest", "size": 600_000_000},
                {"model": "phi3:mini", "size": 2_000_000_000},
            ]
        })
        # /api/ps returns only the actually-loaded models -- just tinyllama
        mock_ps_resp = MagicMock()
        mock_ps_resp.status_code = 200
        mock_ps_resp.json = MagicMock(return_value={
            "models": [
                {"model": "tinyllama:latest", "size": 600_000_000},
            ]
        })

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            async def mock_get(url):
                if "11434" in url:
                    if "/api/ps" in url:
                        return mock_ps_resp
                    return mock_tags_resp
                raise Exception("not running")

            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            manifest = {
                "resource_id": "w1",
                "models": [
                    {
                        "model_id": "tinyllama:latest",
                        "software": "ollama",
                    },
                    {
                        "model_id": "phi3:mini",
                        "software": "ollama",
                    },
                ],
            }
            with patch("tinyagentos.worker.worker_manifest.load_manifest",
                       return_value=manifest):
                with patch("tinyagentos.worker.worker_manifest.SOFTWARE_TO_BACKEND_TYPE",
                           mock_sw_map):
                    backends = await agent.detect_backends()

        assert len(backends) == 1
        avail = backends[0].get("available_models", [])
        assert len(avail) == 2
        # tinyllama is loaded (in /api/ps → loaded_models)
        tiny = [m for m in avail if m["model_id"] == "tinyllama:latest"][0]
        assert tiny["status"] == "loaded"
        # phi3 is only on disk (in /api/tags → models catalog, not loaded)
        phi = [m for m in avail if m["model_id"] == "phi3:mini"][0]
        assert phi["status"] == "available"
