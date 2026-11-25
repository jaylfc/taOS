"""RED tests for routes/storage.py frozen dataclass bug."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.routes.storage import list_disks
from tinyagentos.storage_engine import DiskInfo, SAFETY_AVAILABLE


class TestStorageRouteFrozenDataclass:
    """Tests for the frozen dataclass bug in routes/storage.py:74."""

    @pytest.mark.asyncio
    async def test_list_disks_frozen_dataclass_bug_RED(self):
        """CURRENT BUG: routes/storage.py:74 assigns to frozen dataclass field.
        
        Line 74: disk.by_id_path = await _get_disk_by_id_path(disk.path)
        
        DiskInfo is a frozen dataclass, so this raises FrozenInstanceError.
        """
        # Mock the request object
        mock_request = MagicMock()
        mock_request.app.state.config = MagicMock()

        # Mock require_admin to not raise
        with patch("tinyagentos.routes.storage.require_admin") as mock_require_admin:
            # Mock discover_disks to return a DiskInfo
            with patch("tinyagentos.routes.storage.discover_disks") as mock_discover:
                mock_discover.return_value = [
                    DiskInfo(
                        path="/dev/sdb",
                        name="sdb",
                        make="TestVendor",
                        model="TestModel",
                        serial="TEST123",
                        size_bytes=10_000_000_000,
                        transport="sata",
                        rotational=True,
                        partitions=[],
                        mountpoints=[],
                        fstype=None,
                        uuid=None,
                        label=None,
                        parttable_type=None,
                        ro=False,
                        safety_class=SAFETY_AVAILABLE,
                        safety_reason=None,
                    )
                ]

                # Mock _get_disk_by_id_path
                with patch("tinyagentos.routes.storage._get_disk_by_id_path", return_value="/dev/disk/by-id/test"):
                    # Mock disks_to_json
                    with patch("tinyagentos.routes.storage.disks_to_json") as mock_disks_to_json:
                        mock_disks_to_json.return_value = [{"path": "/dev/sdb", "by_id_path": "/dev/disk/by-id/test"}]

                        # This should raise FrozenInstanceError on current code
                        # because line 74 does: disk.by_id_path = await _get_disk_by_id_path(disk.path)
                        try:
                            result = await list_disks(mock_request)
                            # If we get here without exception, the bug might be fixed
                            # Check that disks_to_json was called with disks that have by_id_path
                            call_args = mock_disks_to_json.call_args[0][0]
                            assert len(call_args) == 1
                            # The disk should have by_id_path attribute set
                            # (but since DiskInfo is frozen, this would have failed)
                            print(f"Result: {result}")
                        except Exception as e:
                            # CURRENT BUG: FrozenInstanceError is raised
                            assert type(e).__name__ == "FrozenInstanceError", f"Expected FrozenInstanceError, got {type(e).__name__}: {e}"
                            # Re-raise to mark test as failed (RED)
                            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v"])