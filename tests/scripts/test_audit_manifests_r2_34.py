"""RED test for lib-audit R2-34: scripts/audit-manifests.py must fail
any service manifest that declares both ``requires.ports`` and
``install.ports``. The two-key duplication was the source of the
docker_installer crash and the silent dead-branch bug (the install-side
list was always ignored when requires.ports was set).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path("/tmp/exec-tsk-idnhkc/scripts")


def _load_audit_module():
    """Load scripts/audit-manifests.py as a module (it has a hyphen in
    its name so importlib.util.spec_from_file_location is needed)."""
    spec = importlib.util.spec_from_file_location(
        "audit_manifests", SCRIPTS_DIR / "audit-manifests.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_audit_mod = _load_audit_module()
audit = _audit_mod.audit


def _write_service(root: Path, name: str, manifest: dict) -> Path:
    svc_dir = root / "services" / name
    svc_dir.mkdir(parents=True, exist_ok=True)
    p = svc_dir / "manifest.yaml"
    p.write_text(yaml.dump(manifest, default_flow_style=False, sort_keys=False))
    return p


@pytest.fixture
def catalog_root(tmp_path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    return root


def test_audit_flags_both_keys(catalog_root):
    """A manifest that declares both requires.ports and install.ports
    must be flagged as a manifest issue by the audit."""
    _write_service(catalog_root, "dup-ports", {
        "id": "dup-ports",
        "name": "Dup Ports",
        "type": "service",
        "requires": {"ram_mb": 256, "disk_mb": 100, "ports": [3000]},
        "install": {
            "method": "docker",
            "image": "example/app:1",
            "ports": [3000],
        },
    })
    rc = audit(catalog_root)
    # Re-run with capture via a side channel: audit() prints to stdout, so
    # we just assert the return code is non-zero AND the manifest's
    # path appears in the captured output.
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = audit(catalog_root)
    output = buf.getvalue()
    assert rc != 0, (
        "audit-manifests must return non-zero when a manifest declares "
        "both requires.ports and install.ports"
    )
    assert "dup-ports" in output, (
        f"audit must name the offending manifest; got:\n{output}"
    )
    assert "R2-34" in output, (
        f"audit message must reference the lib-audit R2-34 finding; got:\n{output}"
    )


def test_audit_passes_with_install_only(catalog_root):
    """A manifest that declares only install.ports (the canonical key
    after the R2-34 migration) must not be flagged for port duplication."""
    _write_service(catalog_root, "install-only", {
        "id": "install-only",
        "name": "Install Only",
        "type": "service",
        "requires": {"ram_mb": 256, "disk_mb": 100},
        "install": {
            "method": "docker",
            "image": "example/app:1",
            "ports": ["3000:3000"],
        },
    })
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = audit(catalog_root)
    output = buf.getvalue()
    assert "install-only" not in output or "R2-34" not in output, (
        f"install-only must not be flagged for port duplication; got:\n{output}"
    )


def test_audit_passes_with_requires_only(catalog_root):
    """A manifest that declares only requires.ports (no install.ports)
    must not be flagged for port duplication."""
    _write_service(catalog_root, "requires-only", {
        "id": "requires-only",
        "name": "Requires Only",
        "type": "service",
        "requires": {"ram_mb": 256, "disk_mb": 100, "ports": [3000]},
        "install": {
            "method": "docker",
            "image": "example/app:1",
        },
    })
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = audit(catalog_root)
    output = buf.getvalue()
    assert "requires-only" not in output or "R2-34" not in output, (
        f"requires-only must not be flagged for port duplication; got:\n{output}"
    )
