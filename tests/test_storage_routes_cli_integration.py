"""Integration tests for storage routes and CLI.

These tests drive the real HTTP routes and CLI entry points using mocked
storage engine functions. No real block devices are accessed.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.cli.taosctl.commands.storage import _add, _areas, _disks, _primary
from tinyagentos.storage_engine import DiskInfo, SAFETY_AVAILABLE


class TestStorageRoutes:
    """Integration tests for storage HTTP routes."""

    @pytest.fixture
    def mock_disk(self):
        """Create a mock available disk."""
        return DiskInfo(
            path="/dev/sdb",
            name="sdb",
            make="TestVendor",
            model="TestModel",
            serial="TEST123456",
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

    @pytest.mark.asyncio
    async def test_list_disks_route_success(self, client, mock_disk):
        """Test GET /api/storage/disks returns disks with by_id_path."""
        with patch("tinyagentos.routes.storage.discover_disks", return_value=[mock_disk]), \
             patch("tinyagentos.routes.storage._get_disk_by_id_path", return_value="/dev/disk/by-id/test"):

            response = await client.get("/api/storage/disks")

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["path"] == "/dev/sdb"
            assert data[0]["by_id_path"] == "/dev/disk/by-id/test"
            assert data[0]["model"] == "TestModel"
            assert data[0]["serial"] == "TEST123456"
            assert data[0]["safety_class"] == "available"

    @pytest.mark.asyncio
    async def test_add_storage_route_success(self, client):
        """Test POST /api/storage/add creates storage area."""
        mock_area = {
            "id": "test-id",
            "name": "teststorage",
            "mount": "/srv/taos/storage/teststorage",
            "size_bytes": 10_000_000_000,
            "free_bytes": 5_000_000_000,
            "encryption": "system-unlock",
            "disk_serial": "TEST123456",
            "disk_path": "/dev/disk/by-id/test",
        }

        with patch("tinyagentos.routes.storage.add_storage_area", return_value={"ok": True, "area": mock_area}), \
             patch("tinyagentos.config.save_config_locked", new_callable=AsyncMock):

            response = await client.post(
                "/api/storage/add",
                json={
                    "disk": "/dev/disk/by-id/test",
                    "confirm": "TestModel TEST123456",
                    "name": "teststorage",
                    "encrypt": "system-unlock",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "teststorage"
            assert data["encryption"] == "system-unlock"

    @pytest.mark.asyncio
    async def test_add_storage_route_refuses_mismatched_confirm(self, client):
        """Test POST /api/storage/add refuses mismatched confirm string."""
        with patch("tinyagentos.routes.storage.add_storage_area", return_value={
            "ok": False,
            "error": "Confirm string does not match disk",
            "expected": "TestModel TEST123456",
            "received": "WrongModel WRONG123",
        }):

            response = await client.post(
                "/api/storage/add",
                json={
                    "disk": "/dev/disk/by-id/test",
                    "confirm": "WrongModel WRONG123",
                    "name": "teststorage",
                    "encrypt": "system-unlock",
                },
            )

            assert response.status_code == 400
            assert "Confirm string does not match" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_add_storage_route_refuses_unavailable_disk(self, client):
        """Test POST /api/storage/add refuses unavailable disk."""
        with patch("tinyagentos.routes.storage.add_storage_area", return_value={
            "ok": False,
            "error": "Disk is not available: has-data",
            "safety_class": "protected",
            "safety_reason": "has-data",
        }):

            response = await client.post(
                "/api/storage/add",
                json={
                    "disk": "/dev/disk/by-id/test",
                    "confirm": "TestModel TEST123456",
                    "name": "teststorage",
                    "encrypt": "system-unlock",
                },
            )

            assert response.status_code == 409
            assert "not available" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_areas_route(self, client):
        """Test GET /api/storage/areas lists registered areas."""
        mock_area = MagicMock()
        mock_area.__dict__ = {
            "id": "test-id",
            "name": "teststorage",
            "mount": "/srv/taos/storage/teststorage",
            "size_bytes": 10_000_000_000,
            "free_bytes": 5_000_000_000,
            "encryption": "system-unlock",
            "disk_serial": "TEST123456",
            "disk_path": "/dev/disk/by-id/test",
            "created_at": 1234567890.0,
        }

        with patch("tinyagentos.routes.storage.list_storage_areas", return_value=[mock_area]):
            response = await client.get("/api/storage/areas")

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["name"] == "teststorage"

    @pytest.mark.asyncio
    async def test_get_primary_route(self, client):
        """Test GET /api/storage/primary/{person_id} returns capacity."""
        mock_capacity = {
            "person_id": "person1",
            "area_name": "teststorage",
            "mount": "/srv/taos/storage/teststorage",
            "size_bytes": 10_000_000_000,
            "free_bytes": 5_000_000_000,
            "used_bytes": 5_000_000_000,
            "encryption": "system-unlock",
        }

        with patch("tinyagentos.routes.storage.get_primary_home_capacity", return_value=mock_capacity):
            response = await client.get("/api/storage/primary/person1")

            assert response.status_code == 200
            data = response.json()
            assert data["person_id"] == "person1"
            assert data["area_name"] == "teststorage"

    @pytest.mark.asyncio
    async def test_get_primary_route_not_found(self, client):
        """Test GET /api/storage/primary/{person_id} returns 404 if no primary."""
        with patch("tinyagentos.routes.storage.get_primary_home_capacity", return_value=None):
            response = await client.get("/api/storage/primary/person1")

            assert response.status_code == 404
            assert "No primary home set" in response.json()["detail"]


class TestStorageCLI:
    """Integration tests for taosctl storage CLI commands."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        client = MagicMock()
        return client

    def test_disks_command_json_output(self, mock_client):
        """Test 'taosctl storage disks --json' outputs JSON."""
        from io import StringIO
        from contextlib import redirect_stdout

        mock_client.get.return_value = [
            {
                "path": "/dev/sdb",
                "by_id_path": "/dev/disk/by-id/test",
                "name": "sdb",
                "size_human": "10.0 GiB",
                "model": "TestModel",
                "serial": "TEST123456",
                "safety_class": "available",
                "safety_reason": None,
            }
        ]

        class Args:
            json = True

        output = StringIO()
        with redirect_stdout(output):
            _disks(Args(), mock_client)

        result = output.getvalue()
        assert "TestModel" in result
        assert "available" in result

    def test_add_command_json_output(self, mock_client):
        """Test 'taosctl storage add --json' outputs JSON."""
        from io import StringIO
        from contextlib import redirect_stdout

        mock_client.post.return_value = {
            "id": "test-id",
            "name": "teststorage",
            "mount": "/srv/taos/storage/teststorage",
            "size_bytes": 10_000_000_000,
            "encryption": "system-unlock",
        }

        class Args:
            disk = "/dev/disk/by-id/test"
            confirm = "TestModel TEST123456"
            name = "teststorage"
            encrypt = "system-unlock"
            json = True

        output = StringIO()
        with redirect_stdout(output):
            _add(Args(), mock_client)

        result = output.getvalue()
        assert "teststorage" in result
        assert "test-id" in result

    def test_add_command_human_output(self, mock_client):
        """Test 'taosctl storage add' outputs human-readable format."""
        from io import StringIO
        from contextlib import redirect_stdout

        mock_client.post.return_value = {
            "id": "test-id",
            "name": "teststorage",
            "mount": "/srv/taos/storage/teststorage",
            "size_bytes": 10_000_000_000,
            "encryption": "system-unlock",
        }

        class Args:
            disk = "/dev/disk/by-id/test"
            confirm = "TestModel TEST123456"
            name = "teststorage"
            encrypt = "system-unlock"
            json = False

        output = StringIO()
        with redirect_stdout(output):
            _add(Args(), mock_client)

        result = output.getvalue()
        assert "Storage area created: teststorage" in result
        assert "ID: test-id" in result

    def test_areas_command_json_output(self, mock_client):
        """Test 'taosctl storage areas --json' outputs JSON."""
        from io import StringIO
        from contextlib import redirect_stdout

        mock_client.get.return_value = [
            {
                "name": "teststorage",
                "mount": "/srv/taos/storage/teststorage",
                "size_bytes": 10_000_000_000,
                "free_bytes": 5_000_000_000,
                "encryption": "system-unlock",
            }
        ]

        class Args:
            json = True

        output = StringIO()
        with redirect_stdout(output):
            _areas(Args(), mock_client)

        result = output.getvalue()
        assert "teststorage" in result

    def test_primary_command_json_output(self, mock_client):
        """Test 'taosctl storage primary --json' outputs JSON."""
        from io import StringIO
        from contextlib import redirect_stdout

        mock_client.get.return_value = {
            "person_id": "person1",
            "area_name": "teststorage",
            "mount": "/srv/taos/storage/teststorage",
            "size_bytes": 10_000_000_000,
            "free_bytes": 5_000_000_000,
            "used_bytes": 5_000_000_000,
            "encryption": "system-unlock",
        }

        class Args:
            person = "person1"
            json = True

        output = StringIO()
        with redirect_stdout(output):
            _primary(Args(), mock_client)

        result = output.getvalue()
        assert "person1" in result
        assert "teststorage" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])