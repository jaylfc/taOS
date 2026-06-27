from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.agent_image import (
    BASE_IMAGE_ALIAS,
    GENERIC_BASE_ALIAS,
    RELEASE_BASE_URL,
    arch_suffix,
    base_image_alias,
    base_image_url,
    ensure_image_present,
    is_image_present,
)


class TestArch:
    def test_base_image_url_contains_alias_and_arch(self):
        url = base_image_url("arm64")
        assert url.startswith(RELEASE_BASE_URL)
        assert BASE_IMAGE_ALIAS in url
        assert "arm64" in url
        assert url.endswith(".tar.gz")

    def test_arch_suffix_normalises_host_arch(self):
        with patch("tinyagentos.agent_image.platform.machine", return_value="aarch64"):
            assert arch_suffix() == "arm64"
        with patch("tinyagentos.agent_image.platform.machine", return_value="x86_64"):
            assert arch_suffix() == "x64"


class TestBaseImageAlias:
    def test_openclaw_keeps_historical_alias(self):
        # Back-compat: openclaw must still resolve to the published alias.
        assert base_image_alias("openclaw") == "taos-openclaw-base"
        assert base_image_alias("openclaw") == BASE_IMAGE_ALIAS

    def test_hermes_has_dedicated_alias(self):
        assert base_image_alias("hermes") == "taos-hermes-base"

    def test_unknown_framework_falls_back_to_generic(self):
        assert base_image_alias("smolagents") == GENERIC_BASE_ALIAS
        assert base_image_alias("smolagents") == "taos-base"
        assert base_image_alias("anything-else") == "taos-base"

    def test_url_default_framework_keeps_openclaw_alias(self):
        # Existing arch-only callers must be unaffected by the new param.
        assert base_image_url("arm64") == base_image_url("arm64", framework=None)
        assert BASE_IMAGE_ALIAS in base_image_url("arm64")

    def test_url_framework_selects_alias(self):
        hermes_url = base_image_url("arm64", framework="hermes")
        assert "taos-hermes-base" in hermes_url
        assert "arm64" in hermes_url
        generic_url = base_image_url("x64", framework="smolagents")
        assert "taos-base-linux-x64" in generic_url


def _fake_proc(returncode: int = 0, stdout: bytes = b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.wait = AsyncMock(return_value=returncode)
    proc.stdout = MagicMock()
    proc.stdout.close = MagicMock()
    return proc


class TestIsImagePresent:
    @pytest.mark.asyncio
    async def test_true_when_alias_listed(self):
        proc = _fake_proc(0, b"taos-openclaw-base\n")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            assert await is_image_present(BASE_IMAGE_ALIAS) is True

    @pytest.mark.asyncio
    async def test_false_when_absent(self):
        proc = _fake_proc(0, b"")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            assert await is_image_present(BASE_IMAGE_ALIAS) is False

    @pytest.mark.asyncio
    async def test_false_when_incus_missing(self):
        async def boom(*_a, **_k):
            raise FileNotFoundError("incus")
        with patch("asyncio.create_subprocess_exec", new=boom):
            assert await is_image_present(BASE_IMAGE_ALIAS) is False

    @pytest.mark.asyncio
    async def test_false_when_incus_errors(self):
        proc = _fake_proc(1, b"daemon down")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            assert await is_image_present(BASE_IMAGE_ALIAS) is False


class TestEnsureImagePresent:
    @pytest.mark.asyncio
    async def test_noop_when_already_present(self):
        with patch(
            "tinyagentos.agent_image.is_image_present", new=AsyncMock(return_value=True)
        ) as mock_present, \
             patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_launch:
            result = await ensure_image_present()
        assert result is True
        mock_present.assert_awaited_once()
        mock_launch.assert_not_called()

    @pytest.mark.asyncio
    async def test_imports_when_missing(self):
        curl_proc = _fake_proc(0, b"")
        incus_proc = _fake_proc(0, b"Image imported with fingerprint abc\n")

        async def _launch(*args, **kwargs):
            if args and args[0] == "curl":
                return curl_proc
            if args and args[0] == "incus":
                return incus_proc
            raise AssertionError(f"unexpected subprocess launch: {args}")

        with patch(
            "tinyagentos.agent_image.is_image_present", new=AsyncMock(return_value=False)
        ), patch("asyncio.create_subprocess_exec", new=_launch):
            ok = await ensure_image_present(url="http://example.test/img.tar.gz")
        assert ok is True
        curl_proc.communicate.assert_awaited()
        incus_proc.communicate.assert_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_when_incus_import_fails(self):
        curl_proc = _fake_proc(0, b"")
        incus_proc = _fake_proc(1, b"import failed")

        async def _launch(*args, **kwargs):
            if args and args[0] == "curl":
                return curl_proc
            return incus_proc

        with patch(
            "tinyagentos.agent_image.is_image_present", new=AsyncMock(return_value=False)
        ), patch("asyncio.create_subprocess_exec", new=_launch):
            ok = await ensure_image_present(url="http://example.test/img.tar.gz")
        assert ok is False

    @pytest.mark.asyncio
    async def test_returns_false_when_curl_errors(self):
        curl_proc = _fake_proc(22, b"")
        incus_proc = _fake_proc(0, b"")

        async def _launch(*args, **kwargs):
            if args and args[0] == "curl":
                return curl_proc
            return incus_proc

        with patch(
            "tinyagentos.agent_image.is_image_present", new=AsyncMock(return_value=False)
        ), patch("asyncio.create_subprocess_exec", new=_launch):
            ok = await ensure_image_present(url="http://example.test/img.tar.gz")
        assert ok is False
