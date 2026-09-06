from pathlib import Path


def is_first_boot(data_dir: Path) -> bool:
    """Check if this is the first boot (no agents configured, no installed apps)."""
    marker = data_dir / ".setup_complete"
    return not marker.exists()


def mark_setup_complete(data_dir: Path) -> None:
    """Mark setup as complete so the first-boot wizard is not shown again."""
    marker = data_dir / ".setup_complete"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
