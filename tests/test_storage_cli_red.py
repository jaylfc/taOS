"""RED tests for CLI storage --json flag missing on add command."""
from unittest.mock import MagicMock, patch

import pytest

from tinyagentos.cli.taosctl.commands.storage import register, _add


class TestStorageCLIJsonFlag:
    """Tests for the missing --json flag on storage add command."""

    def test_add_command_has_json_flag_RED(self):
        """CURRENT BUG: storage add command doesn't have --json flag but _add reads args.json.
        
        Line 66 in storage.py: if args.json:
        
        But the 'add' subparser (lines 19-25) doesn't define --json argument.
        """
        import argparse
        
        # Create a parser and register the storage command
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="noun", required=True)
        register(subparsers)
        
        # Parse 'storage add' command
        args = parser.parse_args(["storage", "add", "--disk", "/dev/test", "--confirm", "model serial", "--name", "test"])
        
        # Check if args has json attribute
        has_json = hasattr(args, 'json')
        
        # CURRENT BUG: args.json doesn't exist (AttributeError when accessed)
        # After fix: args.json should exist (False by default)
        assert has_json, "BUG: 'add' subparser missing --json argument"
        
        # Also test that --json can be passed
        try:
            args_with_json = parser.parse_args(["storage", "add", "--disk", "/dev/test", "--confirm", "model serial", "--name", "test", "--json"])
            assert hasattr(args_with_json, 'json'), "BUG: --json flag not recognized"
            assert args_with_json.json is True, "BUG: --json flag not set to True"
        except SystemExit as e:
            # argparse exits with error if --json is not recognized
            pytest.fail(f"BUG: --json flag not recognized by 'add' subparser: {e}")

    def test_add_function_reads_args_json_RED(self):
        """CURRENT BUG: _add function reads args.json but it doesn't exist."""
        mock_args = MagicMock()
        mock_args.disk = "/dev/disk/by-id/test"
        mock_args.confirm = "TestModel TEST123"
        mock_args.name = "teststorage"
        mock_args.encrypt = "system-unlock"
        # Note: mock_args.json is NOT set (simulating the bug)
        
        mock_client = MagicMock()
        mock_client.post.return_value = {"id": "test-id", "name": "teststorage", "mount": "/mnt/test", "size_bytes": 1000, "encryption": "system-unlock"}
        
        # This should fail with AttributeError: 'Namespace' object has no attribute 'json'
        try:
            result = _add(mock_args, mock_client)
            # If it succeeds, check if json output was handled
            # But currently it will fail
        except AttributeError as e:
            if "'json'" in str(e):
                # This is the expected RED failure
                pytest.fail(f"BUG CONFIRMED: _add reads args.json but it doesn't exist: {e}")
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v"])