import pytest
from tinyagentos.frameworks import FRAMEWORKS, validate_framework_manifest, FrameworkManifestError

def test_openclaw_is_npm_based_no_fork_release_source():
    # OpenClaw installs from npm (openclaw@latest) and is driven over ACP — no
    # fork, so no GitHub-asset release_source/asset_pattern. npm version
    # tracking lands with #570.
    fw = FRAMEWORKS["openclaw"]
    assert "release_source" not in fw
    assert "release_asset_pattern" not in fw
    assert fw["install_script"] == "/usr/local/bin/taos-framework-update"
    assert fw["service_name"] == "openclaw"

def test_validate_rejects_missing_update_fields():
    with pytest.raises(FrameworkManifestError):
        validate_framework_manifest("x", {"id": "x", "name": "X"}, require_update_fields=True)

def test_validate_passes_with_all_fields():
    good = {"id": "x", "name": "X", "release_source": "github:a/b",
            "release_asset_pattern": "b-{arch}.tgz",
            "install_script": "/usr/local/bin/taos-framework-update",
            "service_name": "x"}
    validate_framework_manifest("x", good, require_update_fields=True)

def test_validate_allows_missing_update_fields_when_flag_false():
    validate_framework_manifest("x", {"id": "x", "name": "X"}, require_update_fields=False)

def test_all_frameworks_with_release_source_pass_validation():
    for fw_id, entry in FRAMEWORKS.items():
        if entry.get("release_source"):
            validate_framework_manifest(fw_id, entry, require_update_fields=True)


def test_slash_commands_field_shape():
    """Every framework entry's slash_commands field (if present) is a list of
    {name, description} dicts with non-empty names."""
    for fw_id, entry in FRAMEWORKS.items():
        cmds = entry.get("slash_commands")
        if cmds is None:
            continue
        assert isinstance(cmds, list), f"{fw_id}: slash_commands must be a list"
        for c in cmds:
            assert isinstance(c, dict), f"{fw_id}: each command must be a dict"
            assert c.get("name"), f"{fw_id}: command missing name"
            assert isinstance(c.get("description", ""), str)


def test_hermes_has_slash_commands():
    from tinyagentos.frameworks import FRAMEWORKS
    assert "slash_commands" in FRAMEWORKS["hermes"]
    assert len(FRAMEWORKS["hermes"]["slash_commands"]) > 0
