"""Test to demonstrate the unknown license class issue."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from httpx import ASGITransport, AsyncClient
from tinyagentos.catalog.resolver import DeviceCapability
from taos_test_csrf import csrf_event_hooks


@pytest.mark.asyncio
async def test_unknown_license_class_fails(client):
    """An unknown license class should fail, not fall through to generic text."""
    # Create a mock manifest with UNKNOWN license class
    manifest = MagicMock()
    manifest.id = 'test-model'
    manifest.name = 'Test Model'
    manifest.type = 'model'
    manifest.license_class = 'unknown-class-xyz'  # Not in the allowlist
    manifest.weights_license = 'Some License'
    manifest.license_version = '1.0'
    manifest.license_url = 'https://example.com/license'
    manifest.license_territories = []
    manifest.variants = [{
        'id': 'v1',
        'size_mb': 100,
        'download_url': 'https://example.com/model.bin',
        'requires': {'backends': [{'id': 'ollama', 'targets': ['cpu'], 'min_ram_mb': 512}]}
    }]
    manifest.context_window = 4096
    manifest.hardware_tiers = {}
    manifest.install = {"method": "ollama"}  # Need install method for ollama
    manifest.version = '1.0.0'
    
    registry = MagicMock()
    registry.get_app = MagicMock(return_value=manifest)
    registry.get = MagicMock(return_value=manifest)
    registry.mark_installed = MagicMock()
    registry.list_available = MagicMock(return_value=[])
    client._transport.app.state.registry = registry
    
    cap = DeviceCapability(
        device_id='local',
        targets=('cpu',),
        total_ram_mb=8192,
        total_vram_mb=0,
        free_disk_mb=50000,
        installed_backends=(),
    )
    
    with patch('tinyagentos.routes.store_install.get_device_capability', new=AsyncMock(return_value=cap)), \
         patch('tinyagentos.routes.store_install.get_installer') as mock_get:
        model_inst = MagicMock()
        model_inst.install = AsyncMock(return_value={'success': True})
        mock_get.return_value = model_inst
        
        r = await client.post('/api/store/install-v2', json={'manifest_id': 'test-model'})
        # Currently it falls through to generic text and gates (returns 412)
        # After fix, unknown license class should NOT be in restrictive_classes
        # So this should NOT be gated and should proceed to install (200)
        print(f"Status: {r.status_code}")
        print(f"Body: {r.json()}")
        body = r.json()
        if r.status_code == 412:
            print("CURRENT BEHAVIOR: Unknown class is gated (BUG)")
            print(f"Text: {body.get('text', 'N/A')}")
            assert False, "Unknown license class should not be gated"
        elif r.status_code == 200:
            print("FIXED BEHAVIOR: Unknown class is not gated")
        elif r.status_code == 500:
            print(f"ERROR: {body}")
            assert False, f"Server error: {body}"
        else:
            print(f"OTHER: {r.status_code}")
            assert False, f"Unexpected status: {r.status_code}"
