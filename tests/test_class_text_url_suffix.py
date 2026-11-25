"""Test to demonstrate the class_text f-string bug."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from tinyagentos.catalog.resolver import DeviceCapability
from taos_test_csrf import csrf_event_hooks


@pytest.mark.asyncio
async def test_class_text_url_suffix_rendered(client):
    """The license acceptance text should render {url_suffix} not show it literally."""
    # Create a mock manifest with license_url
    manifest = MagicMock()
    manifest.id = 'test-model'
    manifest.name = 'Test Model'
    manifest.type = 'model'
    manifest.license_class = 'non-commercial'
    manifest.weights_license = 'CC-BY-NC 4.0'
    manifest.license_version = '4.0'
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
    manifest.install = {"method": "ollama"}
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
        assert r.status_code == 412, f"Expected 412, got {r.status_code}: {r.json()}"
        body = r.json()
        assert body["needs_license_acceptance"] is True
        text = body["text"]
        # This should FAIL with the current bug - {url_suffix} appears literally
        assert "{url_suffix}" not in text, f"Literal {{url_suffix}} found in text: {text}"
        assert "https://example.com/license" in text, f"License URL not found in text: {text}"
