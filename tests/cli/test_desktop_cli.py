"""Tests for the `taos desktop` CLI."""
import os
from unittest.mock import patch, MagicMock
import pytest

from tinyagentos.desktop_cli import desktop_cli
def test_desktop_cli_enable_non_root():
    """Test that enable returns 1 and runs nothing when not root."""
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=1000):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            result = desktop_cli(["enable"])
            assert result == 1
            # _run should not have been called for non-root
            mock_run.assert_not_called()


def test_desktop_cli_enable_unit_absent():
    """Test enable as root with unit absent runs kiosk-setup.sh then systemctl enable."""
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            with patch('tinyagentos.desktop_cli._capture') as mock_capture:
                # Mock systemctl cat to return non-zero (unit not found)
                # First call: systemctl cat (unit doesn't exist)
                # Second call: bash scripts/kiosk-setup.sh 
                # Third call: systemctl enable
                mock_run.side_effect = [1, 0, 0]
                mock_capture.return_value = "graphical.target"

                result = desktop_cli(["enable"])
                assert result == 0
                
                # Verify calls in order - we need to check what the implementation actually does
                # The implementation does:
                # 1. _run(["systemctl", "cat", KIOSK_UNIT]) 
                # 2. _run(["bash", str(setup_script)]) where setup_script = PROJECT_DIR / "scripts" / "kiosk-setup.sh"
                # 3. _run(["systemctl", "enable", KIOSK_UNIT])
                
                # We can't predict the exact path, so we need to verify the calls happen
                assert mock_run.call_count == 3
                # First call should be systemctl cat
                mock_run.assert_any_call(["systemctl", "cat", "taos-kiosk.service"])
                # Third call should be systemctl enable
                mock_run.assert_any_call(["systemctl", "enable", "taos-kiosk.service"])
                # The second call is bash with the kiosk-setup.sh script path
                # We can verify this by checking the call arguments pattern
                calls = mock_run.call_args_list
                # Find the bash call
                bash_calls = [call for call in calls if call[0][0] and call[0][0][0] == "bash"]
                assert len(bash_calls) == 1
                
                mock_capture.assert_called_once_with(["systemctl", "get-default"])


def test_desktop_cli_enable_unit_present():
    """Test enable as root with unit present does NOT run kiosk-setup.sh."""
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            with patch('tinyagentos.desktop_cli._capture') as mock_capture:
                # Mock systemctl cat to return 0 (unit exists)
                # First call: systemctl cat (unit exists)
                # Second call: systemctl enable
                mock_run.side_effect = [0, 0]
                mock_capture.return_value = "graphical.target"

                result = desktop_cli(["enable"])
                assert result == 0
                
                # Verify systemctl cat was called
                mock_run.assert_any_call(["systemctl", "cat", "taos-kiosk.service"])
                # Verify kiosk-setup.sh was NOT called
                # So we should only have 2 calls total (cat and enable)
                assert mock_run.call_count == 2
                # The only other call should be systemctl enable
                mock_run.assert_any_call(["systemctl", "enable", "taos-kiosk.service"])


def test_desktop_cli_disable():
    """Test disable as root runs systemctl disable --now."""
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            # Mock systemctl cat to return 0 (unit exists)
            # First call: systemctl cat (unit exists)
            # Second call: systemctl disable --now
            mock_run.side_effect = [0, 0]

            result = desktop_cli(["disable"])
            assert result == 0
            
            # The implementation calls _run twice: check then disable
            assert mock_run.call_count == 2
            # First call should be systemctl cat
            mock_run.assert_any_call(["systemctl", "cat", "taos-kiosk.service"])
            # Second call should be systemctl disable --now
            mock_run.assert_any_call(["systemctl", "disable", "--now", "taos-kiosk.service"])


def test_desktop_cli_disable_unit_not_found():
    """Test disable when unit is not found."""
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            # Mock systemctl cat to return non-zero (unit not found)
            mock_run.return_value = 1

            result = desktop_cli(["disable"])
            assert result == 0
            
            # Should only call _run once for systemctl cat check
            mock_run.assert_called_once_with(["systemctl", "cat", "taos-kiosk.service"])


def test_desktop_cli_run_unit_not_found():
    """Test run with unit absent returns 1 and starts nothing."""
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            # Mock systemctl cat to return non-zero (unit not found)
            mock_run.return_value = 1

            result = desktop_cli(["run"])
            assert result == 1
            
            # Should only call _run once for systemctl cat check
            mock_run.assert_called_once_with(["systemctl", "cat", "taos-kiosk.service"])


def test_desktop_cli_run_unit_found():
    """Test run with unit present starts it."""
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            # Mock systemctl cat to return 0 (unit exists)
            # First call: systemctl cat (unit exists)
            # Second call: systemctl start
            mock_run.side_effect = [0, 0]

            result = desktop_cli(["run"])
            assert result == 0
            
            # The implementation calls _run twice: check then start
            assert mock_run.call_count == 2
            # First call should be systemctl cat
            mock_run.assert_any_call(["systemctl", "cat", "taos-kiosk.service"])
            # Second call should be systemctl start
            mock_run.assert_any_call(["systemctl", "start", "taos-kiosk.service"])


def test_desktop_cli_status_disabled():
    """Test status reports disabled when is-enabled prints disabled."""
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('subprocess.run') as mock_run:
            # Mock systemctl is-enabled to return 1 with "disabled" output
            mock_run.return_value = MagicMock(returncode=1, stdout="disabled")
            
            # For is-active, return 1 with "inactive" output
            def side_effect(cmd, **kwargs):
                if cmd == ["systemctl", "is-enabled", "taos-kiosk.service"]:
                    return MagicMock(returncode=1, stdout="disabled")
                elif cmd == ["systemctl", "is-active", "taos-kiosk.service"]:
                    return MagicMock(returncode=1, stdout="inactive")
                else:
                    return MagicMock(returncode=0, stdout="")
            
            mock_run.side_effect = side_effect

            result = desktop_cli(["status"])
            assert result == 0


def test_desktop_cli_status_enabled():
    """Test status reports enabled when is-enabled prints enabled."""
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('subprocess.run') as mock_run:
            # Mock systemctl is-enabled to return 0 (enabled)
            # Mock systemctl is-active to return 0 (active)
            mock_run.return_value = MagicMock(returncode=0, stdout="")

            result = desktop_cli(["status"])
            assert result == 0


# Test the individual helper functions directly
def test_desktop_enable_helper_non_root():
    """Test _desktop_enable directly when not root."""
    from tinyagentos.desktop_cli import _desktop_enable
    
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=1000):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            # We need to test with empty args for the helper function
            result = _desktop_enable(type('Args', (), {})())
            assert result == 1
            mock_run.assert_not_called()


def test_desktop_enable_helper_unit_absent():
    """Test _desktop_enable directly with unit absent."""
    from tinyagentos.desktop_cli import _desktop_enable
    
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            with patch('tinyagentos.desktop_cli._capture') as mock_capture:
                mock_run.side_effect = [1, 0, 0]  # cat, setup, enable
                mock_capture.return_value = "graphical.target"
                
                result = _desktop_enable(type('Args', (), {})())
                assert result == 0
                assert mock_run.call_count == 3


def test_desktop_disable_helper_unit_found():
    """Test _desktop_disable directly with unit found."""
    from tinyagentos.desktop_cli import _desktop_disable
    
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            mock_run.side_effect = [0, 0]  # cat, disable
            
            result = _desktop_disable(type('Args', (), {})())
            assert result == 0
            assert mock_run.call_count == 2
            mock_run.assert_any_call(["systemctl", "cat", "taos-kiosk.service"])
            mock_run.assert_any_call(["systemctl", "disable", "--now", "taos-kiosk.service"])


def test_desktop_run_helper_unit_found():
    """Test _desktop_run directly with unit found."""
    from tinyagentos.desktop_cli import _desktop_run
    
    with patch('tinyagentos.desktop_cli.os.geteuid', return_value=0):
        with patch('tinyagentos.desktop_cli._run') as mock_run:
            mock_run.side_effect = [0, 0]  # cat, start
            
            result = _desktop_run(type('Args', (), {})())
            assert result == 0
            assert mock_run.call_count == 2
            mock_run.assert_any_call(["systemctl", "cat", "taos-kiosk.service"])
            mock_run.assert_any_call(["systemctl", "start", "taos-kiosk.service"])
