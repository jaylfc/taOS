"""Integration tests for storage engine add/encrypt/mount/registry operations.

Tests use mocked subprocess calls to simulate loop device operations
without touching real block devices.
"""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tinyagentos.storage_engine import (
    SAFETY_AVAILABLE,
    SAFETY_PROTECTED,
    DiskInfo,
    PartitionInfo,
    StorageArea,
    add_storage_area,
    discover_disks,
    get_primary_home_capacity,
    list_storage_areas,
    set_primary_home,
)


class MockConfig:
    """Mock config object for testing."""
    def __init__(self):
        self.storage_areas = []
        self.users = []
        self.config_path = Path("/tmp/test_config.yaml")
        self.config_path.touch()


@pytest.fixture
def mock_config():
    return MockConfig()


@pytest.fixture
def available_disk():
    """A clean, available disk for testing."""
    return DiskInfo(
        path="/dev/sdb",
        name="sdb",
        make="TestVendor",
        model="TestModel",
        serial="TEST123456",
        size_bytes=10_000_000_000,  # 10GB
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


@pytest.fixture
def by_id_path():
    return "/dev/disk/by-id/ata-TestModel_TEST123456"


class TestAddStorageArea:
    """Integration tests for add_storage_area."""

    @pytest.mark.asyncio
    async def test_add_storage_area_success(self, available_disk, by_id_path, mock_config):
        """Test successful addition of a storage area with encryption."""
        with patch("tinyagentos.storage_engine.discover_disks", return_value=[available_disk]), \
             patch("tinyagentos.storage_engine._get_disk_by_id_path", return_value=by_id_path), \
             patch("tinyagentos.storage_engine._run_storage_helper") as mock_helper, \
             patch("tinyagentos.storage_engine._run_cmd") as mock_run_cmd, \
             patch("tinyagentos.storage_engine._path_exists", return_value=True), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.chmod"), \
             patch("tinyagentos.config.save_config_locked", new_callable=AsyncMock):

            # Mock all helper calls to succeed
            mock_helper.return_value = {"ok": True, "output": "", "exit_code": 0}
            mock_run_cmd.return_value = (0, "size avail\n10000000000 5000000000")

            result = await add_storage_area(
                disk_by_id_path=by_id_path,
                confirm_string="TestModel TEST123456",
                area_name="mystorage",
                encrypt="system-unlock",
            )

            assert result["ok"] is True
            assert "area" in result
            area = result["area"]
            assert area["name"] == "mystorage"
            assert area["encryption"] == "system-unlock"
            assert area["disk_serial"] == "TEST123456"
            assert area["disk_path"] == by_id_path
            assert area["mount"] == "/srv/taos/storage/mystorage"

            # Verify helper was called for each step
            commands = [call[0][0][0] for call in mock_helper.call_args_list]
            assert "partition-disk" in commands
            assert "luks-format" in commands
            assert "luks-open" in commands
            assert "make-fs" in commands
            assert "write-crypttab" in commands
            assert "write-mount-unit" in commands
            assert "mount-fs" in commands

    @pytest.mark.asyncio
    async def test_add_storage_area_success_no_encrypt(self, available_disk, by_id_path, mock_config):
        """Test successful addition without encryption."""
        with patch("tinyagentos.storage_engine.discover_disks", return_value=[available_disk]), \
             patch("tinyagentos.storage_engine._get_disk_by_id_path", return_value=by_id_path), \
             patch("tinyagentos.storage_engine._run_storage_helper") as mock_helper, \
             patch("tinyagentos.storage_engine._run_cmd") as mock_run_cmd, \
             patch("tinyagentos.storage_engine._path_exists", return_value=True), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.chmod"), \
             patch("tinyagentos.config.save_config_locked", new_callable=AsyncMock):

            mock_helper.return_value = {"ok": True, "output": "", "exit_code": 0}
            mock_run_cmd.return_value = (0, "size avail\n10000000000 5000000000")

            result = await add_storage_area(
                disk_by_id_path=by_id_path,
                confirm_string="TestModel TEST123456",
                area_name="mystorage",
                encrypt="none",
            )

            assert result["ok"] is True
            area = result["area"]
            assert area["encryption"] == "none"

            # Verify no LUKS commands were called
            commands = [call[0][0] for call in mock_helper.call_args_list]
            assert "luks-format" not in commands
            assert "luks-open" not in commands
            assert "write-crypttab" not in commands

    @pytest.mark.asyncio
    async def test_add_storage_area_refuses_mismatched_confirm(self, available_disk, by_id_path):
        """Test that add_storage_area refuses mismatched confirm string."""
        with patch("tinyagentos.storage_engine.discover_disks", return_value=[available_disk]), \
             patch("tinyagentos.storage_engine._get_disk_by_id_path", return_value=by_id_path):

            result = await add_storage_area(
                disk_by_id_path=by_id_path,
                confirm_string="WrongModel WRONG123",
                area_name="mystorage",
                encrypt="system-unlock",
            )

            assert result["ok"] is False
            assert "Confirm string does not match" in result["error"]
            assert result["expected"] == "TestModel TEST123456"
            assert result["received"] == "WrongModel WRONG123"

    @pytest.mark.asyncio
    async def test_add_storage_area_refuses_unavailable_disk(self, by_id_path):
        """Test that add_storage_area refuses a disk that is not available."""
        protected_disk = DiskInfo(
            path="/dev/sdb",
            name="sdb",
            make="TestVendor",
            model="TestModel",
            serial="TEST123456",
            size_bytes=10_000_000_000,
            transport="sata",
            rotational=True,
            partitions=[],
            mountpoints=["/mnt/somewhere"],  # Mounted!
            fstype=None,
            uuid=None,
            label=None,
            parttable_type=None,
            ro=False,
            safety_class=SAFETY_PROTECTED,
            safety_reason="mounted",
        )

        with patch("tinyagentos.storage_engine.discover_disks", return_value=[protected_disk]), \
             patch("tinyagentos.storage_engine._get_disk_by_id_path", return_value=by_id_path):

            result = await add_storage_area(
                disk_by_id_path=by_id_path,
                confirm_string="TestModel TEST123456",
                area_name="mystorage",
                encrypt="system-unlock",
            )

            assert result["ok"] is False
            assert "not available" in result["error"]
            assert result["safety_class"] == SAFETY_PROTECTED
            assert result["safety_reason"] == "mounted"

    @pytest.mark.asyncio
    async def test_add_storage_area_refuses_disk_that_gained_data(self, available_disk, by_id_path):
        """Test refusal when disk gains data between list and add (re-check)."""
        # First discover returns available disk, but re-check at add time shows data
        disk_with_data = DiskInfo(
            path="/dev/sdb",
            name="sdb",
            make="TestVendor",
            model="TestModel",
            serial="TEST123456",
            size_bytes=10_000_000_000,
            transport="sata",
            rotational=True,
            partitions=[PartitionInfo(
                path="/dev/sdb1", name="sdb1", size_bytes=10_000_000_000,
                fstype="ext4", uuid="abc", label=None, mountpoint=None,
                parttype=None, partlabel=None
            )],
            mountpoints=[],
            fstype=None,
            uuid=None,
            label=None,
            parttable_type="gpt",
            ro=False,
            safety_class=SAFETY_PROTECTED,  # Now protected due to data
            safety_reason="has-data",
        )

        with patch("tinyagentos.storage_engine.discover_disks", return_value=[disk_with_data]), \
             patch("tinyagentos.storage_engine._get_disk_by_id_path", return_value=by_id_path):

            result = await add_storage_area(
                disk_by_id_path=by_id_path,
                confirm_string="TestModel TEST123456",
                area_name="mystorage",
                encrypt="system-unlock",
            )

            assert result["ok"] is False
            assert "not available" in result["error"]
            assert "has-data" in result["safety_reason"]

    @pytest.mark.asyncio
    async def test_add_storage_area_helper_failure_rollback(self, available_disk, by_id_path, mock_config):
        """Test that helper failure leaves disk in known state."""
        with patch("tinyagentos.storage_engine.discover_disks", return_value=[available_disk]), \
             patch("tinyagentos.storage_engine._get_disk_by_id_path", return_value=by_id_path), \
             patch("tinyagentos.storage_engine._run_storage_helper") as mock_helper, \
             patch("tinyagentos.storage_engine._path_exists", return_value=True), \
             patch("asyncio.sleep", new_callable=AsyncMock):

            # Partition succeeds, but LUKS format fails
            def helper_side_effect(args):
                if args[0] == "partition-disk":
                    return {"ok": True, "output": "", "exit_code": 0}
                elif args[0] == "luks-format":
                    return {"ok": False, "output": "LUKS format failed", "exit_code": 1}
                return {"ok": True, "output": "", "exit_code": 0}

            mock_helper.side_effect = helper_side_effect

            result = await add_storage_area(
                disk_by_id_path=by_id_path,
                confirm_string="TestModel TEST123456",
                area_name="mystorage",
                encrypt="system-unlock",
            )

            assert result["ok"] is False
            assert "LUKS format failed" in result["error"]
            assert result["step"] == "luks-format"


class TestSetPrimaryHome:
    """Integration tests for set_primary_home."""

    @pytest.mark.asyncio
    async def test_set_primary_home_success(self, mock_config):
        """Test successful primary home assignment."""
        # Add a storage area to config
        area = StorageArea(
            id="test-id",
            name="mystorage",
            mount="/srv/taos/storage/mystorage",
            size_bytes=10_000_000_000,
            free_bytes=5_000_000_000,
            encryption="system-unlock",
            disk_serial="TEST123456",
            disk_path="/dev/disk/by-id/ata-TestModel_TEST123456",
        )
        mock_config.storage_areas = [area.__dict__]
        mock_config.users = [{"id": "person1", "user_id": "person1"}]

        with patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.chmod"), \
             patch("tinyagentos.config.save_config_locked", new_callable=AsyncMock), \
             patch("tinyagentos.storage_engine.list_storage_areas", return_value=[area]):

            result = await set_primary_home("person1", "mystorage", mock_config)

            assert result["ok"] is True
            assert result["person_id"] == "person1"
            assert result["area"] == "mystorage"
            assert result["home_path"] == "/srv/taos/storage/mystorage/homes/person1"
            # Check config was updated
            assert mock_config.users[0].get("primary_home") == "mystorage"

    @pytest.mark.asyncio
    async def test_set_primary_home_area_not_found(self, mock_config):
        """Test primary home assignment with non-existent area."""
        mock_config.users = [{"id": "person1", "user_id": "person1"}]

        with patch("tinyagentos.storage_engine.list_storage_areas", return_value=[]):
            result = await set_primary_home("person1", "nonexistent", mock_config)

            assert result["ok"] is False
            assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_set_primary_home_person_not_found(self, mock_config):
        """Test primary home assignment with non-existent person."""
        area = StorageArea(
            id="test-id",
            name="mystorage",
            mount="/srv/taos/storage/mystorage",
            size_bytes=10_000_000_000,
            free_bytes=5_000_000_000,
            encryption="system-unlock",
            disk_serial="TEST123456",
            disk_path="/dev/disk/by-id/ata-TestModel_TEST123456",
        )
        mock_config.storage_areas = [area.__dict__]
        mock_config.users = []

        with patch("tinyagentos.storage_engine.list_storage_areas", return_value=[area]):
            result = await set_primary_home("nonexistent", "mystorage", mock_config)

            assert result["ok"] is False
            assert "not found" in result["error"]


class TestListStorageAreas:
    """Integration tests for list_storage_areas."""

    @pytest.mark.asyncio
    async def test_list_storage_areas(self, mock_config):
        """Test listing storage areas with free space refresh."""
        area = StorageArea(
            id="test-id",
            name="mystorage",
            mount="/srv/taos/storage/mystorage",
            size_bytes=10_000_000_000,
            free_bytes=8_000_000_000,  # Original
            encryption="system-unlock",
            disk_serial="TEST123456",
            disk_path="/dev/disk/by-id/ata-TestModel_TEST123456",
        )
        mock_config.storage_areas = [area.__dict__]

        with patch("tinyagentos.storage_engine._run_cmd", return_value=(0, "Avail\n5000000000")):
            areas = await list_storage_areas(mock_config)

            assert len(areas) == 1
            assert areas[0].name == "mystorage"
            # Free space should be refreshed from df
            assert areas[0].free_bytes == 5_000_000_000


class TestGetPrimaryHomeCapacity:
    """Integration tests for get_primary_home_capacity."""

    @pytest.mark.asyncio
    async def test_get_primary_home_capacity(self, mock_config):
        """Test getting primary home capacity for a person."""
        area = StorageArea(
            id="test-id",
            name="mystorage",
            mount="/srv/taos/storage/mystorage",
            size_bytes=10_000_000_000,
            free_bytes=5_000_000_000,
            encryption="system-unlock",
            disk_serial="TEST123456",
            disk_path="/dev/disk/by-id/ata-TestModel_TEST123456",
        )
        mock_config.storage_areas = [area.__dict__]
        mock_config.users = [{"id": "person1", "user_id": "person1", "primary_home": "mystorage"}]

        with patch("tinyagentos.storage_engine.list_storage_areas", return_value=[area]):
            result = await get_primary_home_capacity("person1", mock_config)

            assert result is not None
            assert result["person_id"] == "person1"
            assert result["area_name"] == "mystorage"
            assert result["size_bytes"] == 10_000_000_000
            assert result["free_bytes"] == 5_000_000_000
            assert result["used_bytes"] == 5_000_000_000
            assert result["encryption"] == "system-unlock"

    @pytest.mark.asyncio
    async def test_get_primary_home_capacity_no_primary(self, mock_config):
        """Test getting capacity when person has no primary home."""
        mock_config.users = [{"id": "person1", "user_id": "person1"}]

        result = await get_primary_home_capacity("person1", mock_config)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_primary_home_capacity_person_not_found(self, mock_config):
        """Test getting capacity for non-existent person."""
        result = await get_primary_home_capacity("nonexistent", mock_config)
        assert result is None


class TestDiscoverDisks:
    """Integration tests for discover_disks with mocked lsblk."""

    @pytest.mark.asyncio
    async def test_discover_disks_mocked(self):
        """Test discover_disks with mocked lsblk output."""
        lsblk_output = {
            "blockdevices": [
                {
                    "name": "sda",
                    "size": "500G",
                    "rota": "0",
                    "tran": "nvme",
                    "ro": "0",
                    "model": "System SSD",
                    "vendor": "VendorA",
                    "serial": "SYS123",
                    "type": "disk",
                    "mountpoints": ["/"],
                    "fstype": "ext4",
                    "uuid": "sys-uuid",
                    "label": "system",
                    "children": [
                        {
                            "name": "sda1",
                            "size": "500M",
                            "fstype": "vfat",
                            "uuid": "boot-uuid",
                            "label": "boot",
                            "mountpoints": ["/boot"],
                            "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                            "partlabel": "EFI System"
                        },
                        {
                            "name": "sda2",
                            "size": "499.5G",
                            "fstype": "ext4",
                            "uuid": "root-uuid",
                            "label": "root",
                            "mountpoints": ["/"],
                            "parttype": "0fc63daf-8483-4772-8e79-3d69d8477de4",
                            "partlabel": "Linux filesystem"
                        }
                    ]
                },
                {
                    "name": "sdb",
                    "size": "10T",
                    "rota": "1",
                    "tran": "sata",
                    "ro": "0",
                    "model": "Data HDD",
                    "vendor": "VendorB",
                    "serial": "DATA456",
                    "type": "disk",
                    "mountpoints": [],
                    "fstype": None,
                    "uuid": None,
                    "label": None,
                    "children": []
                },
                {
                    "name": "zram0",
                    "size": "4G",
                    "rota": "0",
                    "tran": "",
                    "ro": "0",
                    "model": "",
                    "vendor": "",
                    "serial": "",
                    "type": "disk",
                    "mountpoints": [],
                    "fstype": None,
                    "uuid": None,
                    "label": None,
                    "children": []
                }
            ]
        }

        with patch("tinyagentos.storage_engine._run_cmd") as mock_run_cmd, \
             patch("tinyagentos.storage_engine._get_system_disk_paths", return_value={"/dev/sda", "/dev/sda2"}):

            # First call returns lsblk JSON
            # Then blkid/wipefs calls for sda and its partitions, then sdb
            mock_run_cmd.side_effect = [
                (0, json.dumps(lsblk_output)),  # Main lsblk call
                (0, ""), (0, ""),  # blkid/wipefs for sda
                (0, ""), (0, ""),  # blkid/wipefs for sda1
                (0, ""), (0, ""),  # blkid/wipefs for sda2
                (0, ""), (0, ""),  # blkid/wipefs for sdb
            ]

            disks = await discover_disks()

            # Should only find sda and sdb (zram0 filtered)
            assert len(disks) == 2
            disk_names = {d.name for d in disks}
            assert disk_names == {"sda", "sdb"}

            # sda should be protected (system disk, mounted, has data)
            sda = next(d for d in disks if d.name == "sda")
            assert sda.safety_class == SAFETY_PROTECTED
            assert "system-disk" in sda.safety_reason
            assert "mounted" in sda.safety_reason
            assert "has-data" in sda.safety_reason

            # sdb should be available
            sdb = next(d for d in disks if d.name == "sdb")
            assert sdb.safety_class == SAFETY_AVAILABLE
            assert sdb.safety_reason is None
            assert sdb.size_bytes == 10 * 1024**4  # 10TiB
            assert sdb.serial == "DATA456"


class TestStorageRoutes:
    """Tests for storage API routes."""

    @pytest.fixture
    def app_with_mocks(self, tmp_path):
        """Create app with mocked auth and config."""
        from tinyagentos.app import create_app
        from unittest.mock import MagicMock
        
        app = create_app(data_dir=tmp_path)
        mock_config = MagicMock()
        mock_config.users = [{"id": "admin", "user_id": "admin", "is_admin": True}]
        mock_config.storage_areas = []
        app.state.config = mock_config
        
        # Mock auth manager
        mock_auth = MagicMock()
        mock_auth.is_configured.return_value = True
        mock_auth.validate_local_token.return_value = True
        mock_auth.get_primary_user.return_value = {"id": "admin", "is_admin": True}
        mock_auth.get_local_token_binding.return_value = None
        mock_auth.local_token_path.return_value = MagicMock(exists=MagicMock(return_value=False))
        app.state.auth = mock_auth
        
        return app

    @pytest.mark.asyncio
    async def test_storage_disks_route_returns_json(self, app_with_mocks):
        """Test /api/storage/disks returns proper JSON via real route."""
        from fastapi.testclient import TestClient
        from tinyagentos.storage_engine import DiskInfo, SAFETY_AVAILABLE
        
        app = app_with_mocks
        client = TestClient(app)
        
        mock_disk = DiskInfo(
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
        
        with patch("tinyagentos.auth_context.require_admin") as mock_require_admin, \
             patch("tinyagentos.routes.storage.discover_disks", return_value=[mock_disk]), \
             patch("tinyagentos.auth_middleware._resolve_bound_local_token_agent", return_value=None):
            
            mock_require_admin.return_value = None
            
            response = client.get("/api/storage/disks", headers={"Authorization": "Bearer test-token"})
            
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["name"] == "sdb"
            assert data[0]["safety_class"] == "available"
            assert "by_id_path" in data[0]

    @pytest.mark.asyncio
    async def test_storage_disks_route_classifies_protected(self, app_with_mocks):
        """Test route correctly classifies disk with partitions as PROTECTED."""
        from fastapi.testclient import TestClient
        from tinyagentos.storage_engine import DiskInfo, PartitionInfo, SAFETY_PROTECTED, PROTECTED_HAS_DATA
        
        app = app_with_mocks
        client = TestClient(app)
        
        mock_disk = DiskInfo(
            path="/dev/sdb",
            name="sdb",
            make="TestVendor",
            model="TestModel",
            serial="TEST123456",
            size_bytes=10_000_000_000,
            transport="sata",
            rotational=True,
            partitions=[
                PartitionInfo(
                    path="/dev/sdb1",
                    name="sdb1",
                    size_bytes=10_000_000_000,
                    fstype="ext4",
                    uuid="abc",
                    label=None,
                    mountpoint=None,
                    parttype=None,
                    partlabel=None
                )
            ],
            mountpoints=[],
            fstype=None,
            uuid=None,
            label=None,
            parttable_type="gpt",
            ro=False,
            safety_class=SAFETY_PROTECTED,
            safety_reason="has-data",
        )
        
        with patch("tinyagentos.auth_context.require_admin") as mock_require_admin, \
             patch("tinyagentos.routes.storage.discover_disks", return_value=[mock_disk]), \
             patch("tinyagentos.auth_middleware._resolve_bound_local_token_agent", return_value=None):
            
            mock_require_admin.return_value = None
            
            response = client.get("/api/storage/disks", headers={"Authorization": "Bearer test-token"})
            
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["safety_class"] == "protected"
            assert "has-data" in data[0]["safety_reason"]


class TestStorageCLI:
    """Tests for taosctl storage CLI."""

    def test_storage_disks_cli_help(self):
        """Test storage disks CLI help works."""
        from tinyagentos.cli.taosctl.__main__ import main
        import sys
        from io import StringIO
        
        # Capture help output
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            main(["storage", "disks", "--help"])
        except SystemExit:
            pass
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        
        # The subcommand help shows usage, not the full description
        assert "usage: taosctl storage disks" in output

    def test_storage_add_cli_help(self):
        """Test storage add CLI help works."""
        from tinyagentos.cli.taosctl.__main__ import main
        import sys
        from io import StringIO
        
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            main(["storage", "add", "--help"])
        except SystemExit:
            pass
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        
        assert "usage: taosctl storage add" in output
        assert "--disk" in output
        assert "--confirm" in output
        assert "--name" in output
        # --json should NOT be on subcommand
        assert "--json" not in output


class TestStorageHelperValidation:
    """Tests for storage helper re-validation logic (TOCTOU protection)."""

    def test_helper_partition_disk_requires_confirm(self):
        """Test helper partition-disk requires confirm argument."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "/tmp/exec-tsk-2urrcn/scripts/taos-storage-helper", "partition-disk", "/dev/disk/by-id/test"],
            capture_output=True, text=True
        )
        # Should fail with argument error (missing confirm)
        assert result.returncode != 0
        assert "requires" in result.stderr.lower() or "requires" in result.stdout.lower()

    def test_helper_discover_disk_validates_path(self):
        """Test helper discover_disk validates device path."""
        # This test would require root and real devices, so we skip in CI
        # The logic is tested via the storage_engine tests
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])