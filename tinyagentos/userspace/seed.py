"""Boot-seeding for first-party bundled .taosapp packages.

Call seed_bundled_apps once during the lifespan, after the userspace store and
apps_root directory are ready. It is idempotent: it only (re)seeds an app when
the entry is missing or the stored version differs from the bundled version.
"""
from __future__ import annotations

import io
import logging
import shutil
import zipfile
from pathlib import Path

from tinyagentos.userspace.package import extract_package, PackageError, parse_manifest

logger = logging.getLogger(__name__)

# Location of the bundled seed apps, relative to this file.
_DEFAULT_SEED_DIR = Path(__file__).resolve().parent / "seed"


def _build_zip_from_dir(source_dir: Path) -> bytes:
    """Build an in-memory .taosapp zip from all files in source_dir."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(source_dir))
    return buf.getvalue()


async def seed_bundled_apps(store, apps_root: Path, seed_dir: Path | None = None) -> None:
    """Seed every subdirectory under seed_dir that contains a manifest.yaml.

    For each such directory:
    1. Parse the manifest to get id + version.
    2. Skip if the app is already installed at the same version.
    3. Otherwise build a .taosapp zip in memory, extract it, then call
       store.install(..., trust="first-party").

    All errors for a single app are caught and logged; they do not abort seeding
    of subsequent apps or crash startup.
    """
    if seed_dir is None:
        seed_dir = _DEFAULT_SEED_DIR
    seed_dir = Path(seed_dir)
    if not seed_dir.is_dir():
        logger.debug("seed_dir %s does not exist, skipping", seed_dir)
        return

    for app_dir in sorted(seed_dir.iterdir()):
        manifest_path = app_dir / "manifest.yaml"
        if not app_dir.is_dir() or not manifest_path.exists():
            continue
        try:
            manifest = parse_manifest(manifest_path.read_text("utf-8"))
            app_id = manifest["id"]
            version = manifest["version"]

            existing = await store.get(app_id)
            if (existing is not None
                    and existing.get("version") == version
                    and existing.get("trust") == "first-party"):
                logger.debug("bundled app %s v%s already installed first-party, skipping", app_id, version)
                continue

            # Re-seed (new app, version bump, or a non-first-party row claiming
            # this id): remove any previously extracted files first so a smaller
            # new version cannot inherit stale files from the old one, then extract.
            shutil.rmtree(apps_root / app_id, ignore_errors=True)
            zip_bytes = _build_zip_from_dir(app_dir)
            extract_package(zip_bytes, apps_root)
            await store.install(
                app_id=app_id,
                name=manifest["name"],
                version=version,
                app_type=manifest["app_type"],
                entry=manifest.get("entry", "index.html"),
                icon=manifest.get("icon", ""),
                permissions_requested=manifest.get("permissions", []),
                trust="first-party",
            )
            logger.info("seeded bundled app %s v%s", app_id, version)
        except (PackageError, Exception):
            logger.warning("failed to seed bundled app in %s", app_dir, exc_info=True)
