"""Tests for the store verification check runner."""

import pytest
from pathlib import Path

import tinyagentos.store_verification as store_verification
from tinyagentos.store_verification import run_checks


def _make_manifest_yaml(text: str, app_dir: Path) -> Path:
    """Write manifest.yaml into app_dir and return the path."""
    p = app_dir / "manifest.yaml"
    p.write_text(text)
    return p


@pytest.fixture
def valid_app_dir(tmp_path: Path) -> Path:
    """A minimal valid userspace app directory (web type)."""
    p = tmp_path / "valid-app"
    p.mkdir()
    _make_manifest_yaml(
        "id: todo\nname: Todo\nversion: 1.0.0\napp_type: web\nentry: index.html\n"
        "permissions: [app.net]\n",
        p,
    )
    (p / "index.html").write_text("<h1>Hello</h1>")
    return p


@pytest.fixture
def missing_manifest_dir(tmp_path: Path) -> Path:
    """An app directory with no manifest file."""
    p = tmp_path / "no-manifest"
    p.mkdir()
    return p


@pytest.fixture
def wildcard_network_dir(tmp_path: Path) -> Path:
    """An app with network:* wildcard permission."""
    p = tmp_path / "net-wild"
    p.mkdir()
    _make_manifest_yaml(
        "id: net-wild\nname: NetWild\nversion: 1.0.0\napp_type: web\n"
        "entry: index.html\npermissions:\n  - 'network: *'\n",
        p,
    )
    (p / "index.html").write_text("<h1>Hello</h1>")
    return p


@pytest.fixture
def port_6969_dir(tmp_path: Path) -> Path:
    """An app declaring port 6969."""
    p = tmp_path / "port-6969"
    p.mkdir()
    _make_manifest_yaml(
        "id: port-6969\nname: Port6969\nversion: 1.0.0\napp_type: web\n"
        "entry: index.html\nports: [6969]\n",
        p,
    )
    (p / "index.html").write_text("<h1>Hello</h1>")
    return p


@pytest.fixture
def missing_entry_dir(tmp_path: Path) -> Path:
    """An app with manifest but missing declared entry file."""
    p = tmp_path / "missing-entry"
    p.mkdir()
    _make_manifest_yaml(
        "id: missing-entry\nname: MissingEntry\nversion: 1.0.0\napp_type: web\n"
        "entry: missing.html\n",
        p,
    )
    return p


@pytest.fixture
def native_app_dir(tmp_path: Path) -> Path:
    """An app with native app_type (non-userspace)."""
    p = tmp_path / "native-app"
    p.mkdir()
    _make_manifest_yaml(
        "id: native-app\nname: NativeApp\nversion: 1.0.0\napp_type: native\n"
        "entry: main.py\n",
        p,
    )
    (p / "main.py").write_text("# native entry")
    return p


class TestRunChecks:
    @pytest.mark.asyncio
    async def test_valid_package_passes_all(self, valid_app_dir: Path):
        result = await run_checks(valid_app_dir)
        assert result["ok"] is True
        for check in result["checks"]:
            assert check["status"] == "pass", f"check {check['name']} expected pass, got {check['status']}: {check['detail']}"

    @pytest.mark.asyncio
    async def test_missing_manifest_fails_manifest_valid(self, missing_manifest_dir: Path):
        result = await run_checks(missing_manifest_dir)
        assert result["ok"] is False
        manifest_check = next(c for c in result["checks"] if c["name"] == "manifest_valid")
        assert manifest_check["status"] == "fail"
        assert "missing or unparseable" in manifest_check["detail"]

    @pytest.mark.asyncio
    async def test_wildcard_network_warns_permissions_scan(self, wildcard_network_dir: Path):
        result = await run_checks(wildcard_network_dir)
        assert result["ok"] is False
        perm_check = next(c for c in result["checks"] if c["name"] == "permissions_scan")
        assert perm_check["status"] == "warn"
        assert "network: *" in perm_check["detail"]

    @pytest.mark.asyncio
    async def test_port_6969_fails_port_hygiene(self, port_6969_dir: Path):
        result = await run_checks(port_6969_dir)
        assert result["ok"] is False
        port_check = next(c for c in result["checks"] if c["name"] == "port_hygiene")
        assert port_check["status"] == "fail"
        assert "6969" in port_check["detail"]

    @pytest.mark.asyncio
    async def test_missing_declared_entry_fails_builds(self, missing_entry_dir: Path):
        result = await run_checks(missing_entry_dir)
        assert result["ok"] is False
        builds_check = next(c for c in result["checks"] if c["name"] == "builds")
        assert builds_check["status"] == "fail"
        assert "missing" in builds_check["detail"].lower()

    @pytest.mark.asyncio
    async def test_non_userspace_app_validates_core_fields(self, native_app_dir: Path):
        """Native app_type should validate core fields directly (not via parse_manifest)."""
        result = await run_checks(native_app_dir)
        assert result["ok"] is True
        manifest_check = next(c for c in result["checks"] if c["name"] == "manifest_valid")
        assert manifest_check["status"] == "pass"

    @pytest.mark.asyncio
    async def test_no_ports_declared_passes_port_hygiene(self, valid_app_dir: Path):
        """No ports declared should pass port hygiene."""
        result = await run_checks(valid_app_dir)
        port_check = next(c for c in result["checks"] if c["name"] == "port_hygiene")
        assert port_check["status"] == "pass"

    @pytest.mark.asyncio
    async def test_low_port_violation_fails_port_hygiene(self, tmp_path: Path):
        """Port < 1024 should fail port hygiene."""
        p = tmp_path / "low-port"
        p.mkdir()
        _make_manifest_yaml(
            "id: low-port\nname: LowPort\nversion: 1.0.0\napp_type: web\n"
            "entry: index.html\nports: [80]\n",
            p,
        )
        (p / "index.html").write_text("<h1>Hello</h1>")
        result = await run_checks(p)
        assert result["ok"] is False
        port_check = next(c for c in result["checks"] if c["name"] == "port_hygiene")
        assert port_check["status"] == "fail"
        assert "reserved low range" in port_check["detail"].lower()

    @pytest.mark.asyncio
    async def test_runner_error_no_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Function must never raise -- on bad input it returns failed checks,
        not an exception. Exercise the runner_error path by forcing an exception."""
        monkeypatch.setattr(
            store_verification,
            "_check_manifest_valid",
            lambda d: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = await run_checks(tmp_path)
        assert result["ok"] is False
        assert any(
            c["name"] == "runner_error" and "boom" in c["detail"]
            for c in result["checks"]
        )

    @pytest.mark.asyncio
    async def test_package_json_build_scripts_passes_builds(self, tmp_path: Path):
        """package.json with scripts.build should pass builds check statically."""
        p = tmp_path / "pkg-build"
        p.mkdir()
        _make_manifest_yaml(
            "id: pkg-build\nname: PkgBuild\nversion: 1.0.0\napp_type: web\n"
            "entry: index.html\n",
            p,
        )
        (p / "index.html").write_text("<h1>Hello</h1>")
        (p / "package.json").write_text('{"scripts": {"build": "echo hello"}}')
        result = await run_checks(p)
        builds_check = next(c for c in result["checks"] if c["name"] == "builds")
        assert builds_check["status"] == "pass"
        assert "scripts.build" in builds_check["detail"]

    @pytest.mark.asyncio
    async def test_entry_escapes_package_dir_fails_builds(self, tmp_path: Path):
        """Entry path that escapes package dir should fail builds check."""
        p = tmp_path / "escape-entry"
        p.mkdir()
        _make_manifest_yaml(
            "id: escape-entry\nname: EscapeEntry\nversion: 1.0.0\napp_type: web\n"
            "entry: ../../etc/passwd\n",
            p,
        )
        result = await run_checks(p)
        assert result["ok"] is False
        builds_check = next(c for c in result["checks"] if c["name"] == "builds")
        assert builds_check["status"] == "fail"
        assert "escapes package dir" in builds_check["detail"]
