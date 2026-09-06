"""Unit tests for tinyagentos/frameworks.py registry integrity and validation edge cases."""
from __future__ import annotations

import pytest

from tinyagentos.frameworks import (
    FRAMEWORKS,
    FrameworkManifestError,
    validate_framework_manifest,
)
from tinyagentos.shortcuts.validation import validate_shortcuts


# ---------------------------------------------------------------------------
# FrameworkManifestError hierarchy
# ---------------------------------------------------------------------------

def test_framework_manifest_error_is_value_error():
    assert issubclass(FrameworkManifestError, ValueError)


def test_framework_manifest_error_caught_as_value_error():
    with pytest.raises(ValueError):
        raise FrameworkManifestError("oops")


# ---------------------------------------------------------------------------
# validate_framework_manifest: missing base fields
# ---------------------------------------------------------------------------

def test_validate_missing_id_raises():
    with pytest.raises(FrameworkManifestError, match="missing required field 'id'"):
        validate_framework_manifest("test", {"name": "Test"})


def test_validate_missing_name_raises():
    with pytest.raises(FrameworkManifestError, match="missing required field 'name'"):
        validate_framework_manifest("test", {"id": "test"})


def test_validate_missing_id_includes_fw_id_in_message():
    with pytest.raises(FrameworkManifestError, match="'myfw'"):
        validate_framework_manifest("myfw", {"name": "MyFW"})


def test_validate_empty_dict_raises_missing_id():
    with pytest.raises(FrameworkManifestError):
        validate_framework_manifest("empty", {})


# ---------------------------------------------------------------------------
# validate_framework_manifest: require_update_fields
# ---------------------------------------------------------------------------

def test_validate_update_fields_all_missing():
    with pytest.raises(FrameworkManifestError, match="missing update fields"):
        validate_framework_manifest("x", {"id": "x", "name": "X"}, require_update_fields=True)


def test_validate_update_fields_partial_missing():
    partial = {
        "id": "x",
        "name": "X",
        "release_source": "github:a/b",
        "install_script": "/bin/true",
    }
    with pytest.raises(FrameworkManifestError, match="release_asset_pattern"):
        validate_framework_manifest("x", partial, require_update_fields=True)


def test_validate_update_fields_empty_list_message():
    with pytest.raises(FrameworkManifestError) as exc_info:
        validate_framework_manifest("x", {"id": "x", "name": "X"}, require_update_fields=True)
    msg = str(exc_info.value)
    assert "release_source" in msg
    assert "release_asset_pattern" in msg
    assert "install_script" in msg
    assert "service_name" in msg


def test_validate_update_fields_flag_false_allows_missing():
    validate_framework_manifest("x", {"id": "x", "name": "X"}, require_update_fields=False)


def test_validate_update_fields_flag_default_is_false():
    validate_framework_manifest("x", {"id": "x", "name": "X"})


# ---------------------------------------------------------------------------
# validate_framework_manifest: shortcuts validation integration
# ---------------------------------------------------------------------------

def test_validate_rejects_shortcut_bad_kind():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": [{"kind": "unknown-kind", "label": "L", "icon": "i", "requires_capability": "c"}],
    }
    with pytest.raises(FrameworkManifestError, match="unknown shortcut kind"):
        validate_framework_manifest("x", entry)


def test_validate_rejects_shortcut_missing_label():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": [{"kind": "tui", "icon": "i", "requires_capability": "c", "command": "cmd"}],
    }
    with pytest.raises(FrameworkManifestError, match="missing required field 'label'"):
        validate_framework_manifest("x", entry)


def test_validate_rejects_tui_shortcut_missing_command():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": [{"kind": "tui", "label": "L", "icon": "i", "requires_capability": "c"}],
    }
    with pytest.raises(FrameworkManifestError, match="'command' is required for kind='tui'"):
        validate_framework_manifest("x", entry)


def test_validate_rejects_tui_shortcut_empty_command():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": [
            {"kind": "tui", "label": "L", "icon": "i", "requires_capability": "c", "command": "   "},
        ],
    }
    with pytest.raises(FrameworkManifestError, match="non-empty string"):
        validate_framework_manifest("x", entry)


def test_validate_rejects_dashboard_shortcut_missing_port():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": [
            {
                "kind": "dashboard",
                "label": "L",
                "icon": "i",
                "requires_capability": "c",
                "auth": {"type": "bearer"},
            },
        ],
    }
    with pytest.raises(FrameworkManifestError, match="'port' is required for kind='dashboard'"):
        validate_framework_manifest("x", entry)


def test_validate_rejects_dashboard_shortcut_bad_port():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": [
            {
                "kind": "dashboard",
                "label": "L",
                "icon": "i",
                "requires_capability": "c",
                "port": 0,
                "auth": {"type": "bearer"},
            },
        ],
    }
    with pytest.raises(FrameworkManifestError, match="positive integer"):
        validate_framework_manifest("x", entry)


def test_validate_rejects_dashboard_shortcut_missing_auth():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": [
            {
                "kind": "dashboard",
                "label": "L",
                "icon": "i",
                "requires_capability": "c",
                "port": 8080,
            },
        ],
    }
    with pytest.raises(FrameworkManifestError, match="'auth' is required for kind='dashboard'"):
        validate_framework_manifest("x", entry)


def test_validate_rejects_dashboard_shortcut_bad_auth_type():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": [
            {
                "kind": "dashboard",
                "label": "L",
                "icon": "i",
                "requires_capability": "c",
                "port": 8080,
                "auth": {"type": "oauth"},
            },
        ],
    }
    with pytest.raises(FrameworkManifestError, match="auth.type must be"):
        validate_framework_manifest("x", entry)


def test_validate_rejects_shortcut_not_a_dict():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": ["not-a-dict"],
    }
    with pytest.raises(FrameworkManifestError, match="expected dict"):
        validate_framework_manifest("x", entry)


def test_validate_accepts_valid_shortcuts():
    entry = {
        "id": "x",
        "name": "X",
        "shortcuts": [
            {"kind": "container-terminal", "label": "Shell", "icon": "terminal", "requires_capability": "agent.shell"},
            {"kind": "tui", "label": "TUI", "icon": "tui", "requires_capability": "agent.terminal", "command": "myfw tui"},
            {
                "kind": "dashboard",
                "label": "Dash",
                "icon": "dashboard",
                "requires_capability": "agent.dashboard",
                "port": 18789,
                "path": "/",
                "auth": {"type": "bearer"},
            },
        ],
    }
    validate_framework_manifest("x", entry)


def test_validate_no_shortcuts_field_ok():
    validate_framework_manifest("x", {"id": "x", "name": "X"})


def test_validate_empty_shortcuts_list_ok():
    validate_framework_manifest("x", {"id": "x", "name": "X", "shortcuts": []})


# ---------------------------------------------------------------------------
# FRAMEWORKS registry: structural integrity
# ---------------------------------------------------------------------------

def test_registry_is_dict():
    assert isinstance(FRAMEWORKS, dict)


def test_registry_not_empty():
    assert len(FRAMEWORKS) > 0


def test_all_entries_have_id():
    for fw_id, entry in FRAMEWORKS.items():
        assert "id" in entry, f"{fw_id}: missing 'id'"


def test_all_entries_have_name():
    for fw_id, entry in FRAMEWORKS.items():
        assert "name" in entry, f"{fw_id}: missing 'name'"


def test_all_ids_match_dict_keys():
    for fw_id, entry in FRAMEWORKS.items():
        assert entry.get("id") == fw_id, f"{fw_id}: id field {entry.get('id')!r} != key {fw_id!r}"


def test_all_ids_are_unique():
    ids = [e["id"] for e in FRAMEWORKS.values()]
    assert len(ids) == len(set(ids)), "duplicate ids in FRAMEWORKS"


def test_all_verification_statuses_valid():
    valid = {"tested", "beta", "experimental", "broken"}
    for fw_id, entry in FRAMEWORKS.items():
        status = entry.get("verification_status")
        assert status in valid, f"{fw_id}: unexpected verification_status {status!r}"


def test_all_names_are_non_empty_strings():
    for fw_id, entry in FRAMEWORKS.items():
        name = entry["name"]
        assert isinstance(name, str) and name.strip(), f"{fw_id}: name must be non-empty string"


def test_all_descriptions_are_non_empty_strings():
    for fw_id, entry in FRAMEWORKS.items():
        desc = entry.get("description")
        assert isinstance(desc, str) and desc.strip(), f"{fw_id}: description must be non-empty string"


def test_shortcuts_field_is_list_when_present():
    for fw_id, entry in FRAMEWORKS.items():
        shortcuts = entry.get("shortcuts")
        if shortcuts is not None:
            assert isinstance(shortcuts, list), f"{fw_id}: shortcuts must be a list"


def test_all_shortcuts_pass_validation():
    for fw_id, entry in FRAMEWORKS.items():
        shortcuts = entry.get("shortcuts")
        if shortcuts is not None:
            try:
                validate_shortcuts(shortcuts)
            except ValueError as exc:
                pytest.fail(f"{fw_id}: shortcuts validation failed: {exc}")


def test_slash_commands_shape_when_present():
    for fw_id, entry in FRAMEWORKS.items():
        cmds = entry.get("slash_commands")
        if cmds is None:
            continue
        assert isinstance(cmds, list), f"{fw_id}: slash_commands must be a list"
        for i, cmd in enumerate(cmds):
            assert isinstance(cmd, dict), f"{fw_id}: slash_commands[{i}] must be dict"
            assert cmd.get("name"), f"{fw_id}: slash_commands[{i}] missing name"
            assert isinstance(cmd.get("description", ""), str)


def test_frameworks_with_update_fields_pass_validation():
    for fw_id, entry in FRAMEWORKS.items():
        if entry.get("release_source"):
            validate_framework_manifest(fw_id, entry, require_update_fields=True)


def test_frameworks_without_release_source_skip_update_fields():
    for fw_id, entry in FRAMEWORKS.items():
        if not entry.get("release_source"):
            validate_framework_manifest(fw_id, entry, require_update_fields=False)


# ---------------------------------------------------------------------------
# OpenClaw-specific integrity (the one entry with dashboard shortcut)
# ---------------------------------------------------------------------------

def test_openclaw_dashboard_shortcut_has_token_source():
    entry = FRAMEWORKS["openclaw"]
    dashboards = [s for s in entry["shortcuts"] if s["kind"] == "dashboard"]
    assert len(dashboards) == 1
    dash = dashboards[0]
    assert "auth" in dash
    auth = dash["auth"]
    assert auth["type"] == "bearer"
    token_source = auth["token_source"]
    assert token_source["kind"] == "container_file"
    assert token_source["path"].endswith("openclaw.json")
    assert token_source["json_pointer"].startswith("/")


def test_openclaw_dashboard_port():
    entry = FRAMEWORKS["openclaw"]
    dash = [s for s in entry["shortcuts"] if s["kind"] == "dashboard"][0]
    assert dash["port"] == 18789


# ---------------------------------------------------------------------------
# Registry completeness: expected frameworks present
# ---------------------------------------------------------------------------

EXPECTED_FRAMEWORKS = {
    "openclaw", "smolagents", "generic", "pocketflow", "langroid",
    "openai-agents-sdk", "hermes", "opencrabs", "agent_zero", "ironclaw",
    "microclaw", "moltis", "nanoclaw", "nullclaw", "picoclaw", "shibaclaw",
    "zeroclaw",
}


def test_all_expected_frameworks_present():
    missing = EXPECTED_FRAMEWORKS - set(FRAMEWORKS.keys())
    assert not missing, f"missing frameworks: {missing}"


def test_no_unexpected_frameworks():
    extra = set(FRAMEWORKS.keys()) - EXPECTED_FRAMEWORKS
    assert not extra, f"unexpected frameworks: {extra}"


def test_registry_has_exactly_expected_count():
    assert len(FRAMEWORKS) == len(EXPECTED_FRAMEWORKS)
