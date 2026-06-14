"""Tests for the rkllama installer.

Most coverage is around the HF URL parser since that's where install
failures will manifest if a manifest URL changes shape. The actual HTTP
roundtrip to /api/pull is exercised manually on the Pi (see PR description).
"""
from __future__ import annotations

import httpx
import pytest
import respx

from tinyagentos.installers.rkllama_installer import (
    RkllamaInstaller,
    parse_hf_resolve_url,
    resolve_rkllama_url,
    rkllama_is_running,
)
from tinyagentos.installers import rkllama_installer


class TestRkllamaIsRunning:
    def test_true_when_taos_port_responds(self, monkeypatch):
        monkeypatch.setattr(
            rkllama_installer, "_port_responds_with_rkllama",
            lambda port, timeout=1.0: port == rkllama_installer._DEFAULT_RKLLAMA_PORT,
        )
        assert rkllama_is_running() is True

    def test_true_when_only_legacy_port_responds(self, monkeypatch):
        monkeypatch.setattr(
            rkllama_installer, "_port_responds_with_rkllama",
            lambda port, timeout=1.0: port == rkllama_installer._LEGACY_RKLLAMA_PORT,
        )
        assert rkllama_is_running() is True

    def test_false_when_nothing_responds(self, monkeypatch):
        monkeypatch.setattr(
            rkllama_installer, "_port_responds_with_rkllama",
            lambda port, timeout=1.0: False,
        )
        assert rkllama_is_running() is False


class TestParseHfResolveUrl:
    def test_standard_main_branch(self):
        url = (
            "https://huggingface.co/c01zaut/Qwen2.5-3B-Instruct-rk3588-1.1.1/"
            "resolve/main/Qwen2.5-3B-Instruct-rk3588-w8a8-opt-1-hybrid-ratio-1.0.rkllm"
        )
        user, repo, filename = parse_hf_resolve_url(url)
        assert user == "c01zaut"
        assert repo == "Qwen2.5-3B-Instruct-rk3588-1.1.1"
        assert filename == "Qwen2.5-3B-Instruct-rk3588-w8a8-opt-1-hybrid-ratio-1.0.rkllm"

    def test_non_main_branch(self):
        url = (
            "https://huggingface.co/user/repo-name/resolve/v2/model.rkllm"
        )
        user, repo, filename = parse_hf_resolve_url(url)
        assert user == "user"
        assert repo == "repo-name"
        assert filename == "model.rkllm"

    def test_http_scheme_accepted(self):
        url = "http://huggingface.co/u/r/resolve/main/f.rkllm"
        user, repo, filename = parse_hf_resolve_url(url)
        assert (user, repo, filename) == ("u", "r", "f.rkllm")

    def test_filename_with_dashes_and_underscores(self):
        url = (
            "https://huggingface.co/GatekeeperZA/Qwen3-1.7B-RKLLM-v1.2.3/"
            "resolve/main/Qwen3-1.7B-w8a8-rk3588.rkllm"
        )
        _, _, filename = parse_hf_resolve_url(url)
        assert filename == "Qwen3-1.7B-w8a8-rk3588.rkllm"

    def test_non_huggingface_url_raises(self):
        with pytest.raises(ValueError, match="HuggingFace"):
            parse_hf_resolve_url("https://example.com/foo/bar/baz.rkllm")

    def test_missing_resolve_segment_raises(self):
        # Direct file URL without /resolve/<branch>/ — common malformed case.
        with pytest.raises(ValueError, match="HuggingFace"):
            parse_hf_resolve_url("https://huggingface.co/user/repo/main/file.rkllm")

    def test_query_string_or_fragment_rejected(self):
        # The model field that gets POSTed to rkllama can't contain ? or #
        # since rkllama splits on / and uses parts directly. Any URL with
        # those should fail validation up front.
        with pytest.raises(ValueError):
            parse_hf_resolve_url(
                "https://huggingface.co/u/r/resolve/main/f.rkllm?x=1"
            )


class TestResolveRkllamaUrl:
    """resolve_rkllama_url for local targets delegates to default_rkllama_url(),
    which probes the network.  Monkeypatch _port_responds_with_rkllama to keep
    tests hermetic (nothing listening -> returns 7833 default).
    """

    def test_none_returns_loopback_7833(self, monkeypatch):
        import tinyagentos.installers.rkllama_installer as mod
        monkeypatch.setattr(mod, "_port_responds_with_rkllama", lambda port, timeout=1.0: False)
        assert resolve_rkllama_url(None) == "http://localhost:7833"

    def test_empty_returns_loopback_7833(self, monkeypatch):
        import tinyagentos.installers.rkllama_installer as mod
        monkeypatch.setattr(mod, "_port_responds_with_rkllama", lambda port, timeout=1.0: False)
        assert resolve_rkllama_url("") == "http://localhost:7833"

    def test_local_returns_loopback_7833(self, monkeypatch):
        import tinyagentos.installers.rkllama_installer as mod
        monkeypatch.setattr(mod, "_port_responds_with_rkllama", lambda port, timeout=1.0: False)
        assert resolve_rkllama_url("local") == "http://localhost:7833"

    def test_remote_name_becomes_url_7833(self):
        assert resolve_rkllama_url("orange-pi") == "http://orange-pi:7833"

    def test_ip_address_7833(self):
        assert resolve_rkllama_url("192.168.1.10") == "http://192.168.1.10:7833"


_VARIANT = {
    "id": "qwen2.5-3b",
    "download_url": (
        "https://huggingface.co/c01zaut/Qwen2.5-3B-Instruct-rk3588-1.1.1/"
        "resolve/main/Qwen2.5-3B-Instruct-rk3588-w8a8.rkllm"
    ),
}


class TestInstallVerification:
    """install() must only report success once /api/tags confirms the model.

    A 200 from /api/pull alone is necessary but not sufficient -- a model the
    agent can't load is worse than a clear error, so an unconfirmable pull
    fails rather than returning a false success.
    """

    def _installer(self):
        # Pass an explicit URL so __init__ doesn't probe the network.
        return RkllamaInstaller(rkllama_url="http://localhost:7833")

    @respx.mock
    @pytest.mark.asyncio
    async def test_success_when_tags_lists_model(self):
        respx.post("http://localhost:7833/api/pull").mock(
            return_value=httpx.Response(200, text='{"status":"success"}\n')
        )
        respx.get("http://localhost:7833/api/tags").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "rkllama-x"}]})
        )
        res = await self._installer().install("rkllama-x", {}, variant=_VARIANT)
        assert res["success"] is True
        assert res["model_name"] == "rkllama-x"

    @respx.mock
    @pytest.mark.asyncio
    async def test_failure_when_model_absent_from_tags(self):
        respx.post("http://localhost:7833/api/pull").mock(
            return_value=httpx.Response(200, text='{"status":"success"}\n')
        )
        respx.get("http://localhost:7833/api/tags").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "other"}]})
        )
        res = await self._installer().install("rkllama-x", {}, variant=_VARIANT)
        assert res["success"] is False
        assert "not in" in res["error"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_failure_when_tags_unreachable(self, monkeypatch):
        # Previously this path returned a false success. Now an unreachable
        # /api/tags (after retries) is a clean failure.
        monkeypatch.setattr(rkllama_installer.asyncio, "sleep", _no_sleep)
        respx.post("http://localhost:7833/api/pull").mock(
            return_value=httpx.Response(200, text='{"status":"success"}\n')
        )
        respx.get("http://localhost:7833/api/tags").mock(
            side_effect=httpx.ConnectError("refused")
        )
        res = await self._installer().install("rkllama-x", {}, variant=_VARIANT)
        assert res["success"] is False
        assert "could not be reached" in res["error"]


async def _no_sleep(*_a, **_k):
    return None


class TestRkllamaServiceManifest:
    """The rkllama service manifest (install.method: script) must point at a
    script that actually exists. ScriptInstaller resolves install.script
    relative to the repo root (its cwd), so a missing file means the store
    install fails with 'script not found'. This is the #844 regression guard.
    """

    def test_install_script_exists(self):
        import pathlib
        import yaml

        repo = pathlib.Path(__file__).resolve().parent.parent
        manifest = yaml.safe_load(
            (repo / "app-catalog" / "services" / "rkllama" / "manifest.yaml").read_text()
        )
        install = manifest.get("install") or {}
        assert install.get("method") == "script"
        script = install.get("script")
        assert script, "rkllama manifest declares no install.script"
        assert (repo / script).is_file(), f"rkllama install script missing: {script}"
