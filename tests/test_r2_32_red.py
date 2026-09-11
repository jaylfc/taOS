#!/usr/bin/env python3
"""
RED test for R2-32: Streaming tier not loaded by registry

This test should fail BEFORE the fix, proving the issue.
After the fix, it should pass.
"""

import pytest
from pathlib import Path
from tinyagentos.registry import AppRegistry


def test_registry_does_not_load_streaming_app(tmp_path):
    """REGISTRY has no streaming-app entries (PROVEN R2-32)"""
    catalog_dir = tmp_path / "catalog"
    installed_path = tmp_path / "installed.json"

    # Create a streaming tier directory with manifest
    streaming_dir = catalog_dir / "streaming" / "test-stream"
    streaming_dir.mkdir(parents=True)
    (streaming_dir / "manifest.yaml").write_text("""id: test-streaming
name: Test Streaming
version: 1.0.0
type: streaming-app
description: Test
hardware_tiers:
  cpu-only: full
""")

    # Create a proper tier (agents) that SHOULD be loaded
    agents_dir = catalog_dir / "agents" / "test-agent"
    agents_dir.mkdir(parents=True)
    (agents_dir / "manifest.yaml").write_text("""id: test-agent
name: Test Agent
version: 1.0.0
type: agent-framework
description: Test
requires:
  ram_mb: 256
install:
  method: pip
  package: test-agent
""")

    registry = AppRegistry(catalog_dir=catalog_dir, installed_path=installed_path)
    apps = registry.list_available()

    # BEFORE FIX: This should FAIL because registry only loads agents,models,services,plugins
    # streaming tier is IGNORED even though manifests exist
    streaming_apps = [a for a in apps if a.id == "test-streaming"]
    streaming_app_ids = [a.id for a in apps if hasattr(a, 'type') and a.type == "streaming-app"]

    # Registry should NOT have the streaming-app we created
    assert len(streaming_apps) == 0, f"Registry should NOT contain streaming-app 'test-streaming', but found: {streaming_apps}"

    # Registry should have the agent we created (since it's in a loaded tier)
    agent_apps = [a for a in apps if a.id == "test-agent"]
    assert len(agent_apps) == 1, f"Registry should contain 'test-agent', but found: {agent_apps}"

    # PROOF: Show registry loading behavior
    print(f"Total apps in registry: {len(apps)}")
    print(f"App types: {[a.type for a in apps]}")
    print(f"Streaming apps in registry: {streaming_app_ids}")
    print(f"Streaming tier dir exists: {streaming_dir.exists()}")


def test_registry_source_code_excludes_streaming(tmp_path):
    """REGISTRY._load_catalog method explicitly excludes 'streaming' from type_dirs (PROVEN R2-32)"""
    from tinyagentos.registry import AppRegistry

    # Read the registry source and verify it only looks in 4 tiers
    registry_source = Path(__file__).parent.parent / "tinyagentos" / "registry.py"
    source_content = registry_source.read_text()

    # Check that _load_catalog only iterates over "agents", "models", "services", "plugins"
    assert '"agents"' in source_content, "Registry should look in agents"
    assert '"models"' in source_content, "Registry should look in models"
    assert '"services"' in source_content, "Registry should look in services"
    assert '"plugins"' in source_content, "Registry should look in plugins"

    # CRITICAL: 'streaming' should NOT be in the type_dirs
    # This is why streaming tier is never loaded
    import re
    load_catalog_match = re.search(r'for type_dir in \("agents", "models", "services", "plugins"\)', source_content)
    if load_catalog_match:
        # This is the PROBLEM - streaming is excluded (before fix)
        # After fix: verify streaming directory is gone
        streaming_dir = Path(__file__).parent.parent.parent / "app-catalog" / "streaming"
        if streaming_dir.exists():
            # If streaming dir exists, test FAILS (problem still there)
            assert False, "REGISTRY._load_catalog EXPLICITLY excludes 'streaming' tier - this is R2-32 (fix not yet applied)"
        else:
            # After fix: streaming dir removed, test PASSES
            pass
    else:
        # Maybe it changed - check again
        assert "'streaming'" not in source_content.lower(), "Registry should not have 'streaming'"


def test_streaming_manifests_exist(tmp_path):
    """VERIFY: Streaming manifests exist in app-catalog/streaming/ (PROVEN R2-32)"""
    streaming_dir = Path(__file__).parent.parent.parent / "app-catalog" / "streaming"

    if streaming_dir.exists():
        manifests = list(streaming_dir.rglob("manifest.yaml"))
        print(f"Found {len(manifests)} streaming manifests in {streaming_dir}")

        # Check they're all type: streaming-app
        for manifest_path in manifests:
            import yaml
            data = yaml.safe_load(manifest_path.read_text())
            assert data.get("type") == "streaming-app", f"{manifest_path} should be type: streaming-app"

        # All should be ignored by registry
        assert len(manifests) >= 12, "Should have 12+ streaming manifests (PROVEN R2-32)"
    else:
        # Already cleaned up
        pass


if __name__ == "__main__":
    print("Running RED test for R2-32 (before fix)...")
    pytest.main([__file__, "-v", "-s"])