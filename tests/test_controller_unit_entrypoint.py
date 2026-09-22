"""Controller systemd units must start via the module entrypoint, not uvicorn directly.

Every restart on a Pi 4 ended in SIGKILL because three of four units bypassed
``tinyagentos/__main__.py``, so the bounded graceful-shutdown handler never ran.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _service_files() -> list[Path]:
    service_files = sorted(REPO_ROOT.rglob("*.service"))
    install_sh = REPO_ROOT / "install.sh"
    if install_sh.exists():
        service_files.append(install_sh)
    return service_files


def _exec_start_values(unit_text: str, is_install_sh: bool = False) -> list[str]:
    values: list[str] = []
    if is_install_sh:
        in_install = False
        for raw_line in unit_text.splitlines():
            stripped = raw_line.strip()
            if not in_install and stripped.startswith("cat > /etc/systemd/system/tinyagentos.service << EOF"):
                in_install = True
                continue
            if in_install:
                if stripped == "EOF":
                    break
                if stripped.startswith("ExecStart="):
                    value = stripped.split("=", 1)[1].strip()
                    values.append(value)
        return values

    in_service = False
    pending: str | None = None
    for raw_line in unit_text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_service = stripped == "[Service]"
            if pending is not None:
                values.append(pending)
                pending = None
            continue
        if in_service and stripped.startswith("ExecStart="):
            value = stripped.split("=", 1)[1].strip()
            if value.endswith("\\"):
                pending = value[:-1].strip()
                continue
            if pending is not None:
                value = pending + " " + value
                pending = None
            values.append(value)
    if pending is not None:
        values.append(pending)
    return values


def test_units_start_via_module_entry() -> None:
    bad: list[tuple[Path, str]] = []
    for unit_path in _service_files():
        text = unit_path.read_text()
        is_install_sh = unit_path.name == "install.sh"
        for value in _exec_start_values(text, is_install_sh):
            if "tinyagentos.app" in value or "-m tinyagentos" in value:
                if "-m tinyagentos" not in value or "-m uvicorn" in value:
                    bad.append((unit_path, value))

    assert not bad, (
        "The following controller unit(s) must use ``-m tinyagentos`` "
        "instead of ``-m uvicorn``:\n"
        + "\n".join(
            f"  {path.relative_to(REPO_ROOT)}: {value!r}"
            for path, value in bad
        )
    )
