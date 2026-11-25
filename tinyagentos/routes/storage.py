"""HTTP API for taOS Storage Engine.

Endpoints:
- GET  /api/storage/disks           -- List block devices with safety classification
- POST /api/storage/add             -- Add disk as encrypted storage area
- POST /api/storage/set-primary     -- Set storage area as person's primary home
- GET  /api/storage/areas           -- List registered storage areas
- GET  /api/storage/primary/<person_id> -- Get primary home capacity for person
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tinyagentos.auth_context import current_user, require_admin
from tinyagentos.storage_engine import (
    _get_disk_by_id_path,
    add_storage_area,
    discover_disks,
    disks_to_json,
    get_primary_home_capacity,
    list_storage_areas,
    set_primary_home,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Request/Response Models ---

class DiskAddRequest(BaseModel):
    disk: str = Field(..., description="Block device by-id path (e.g., /dev/disk/by-id/ata-Samsung_SSD_123)")
    confirm: str = Field(..., description="Exact confirmation string: '<model> <serial>'")
    name: str = Field(..., description="Storage area name (alphanumeric, hyphens, underscores)")
    encrypt: Literal["system-unlock", "none"] = Field(
        default="system-unlock",
        description="Encryption mode: system-unlock (LUKS2 with key in /etc/taos/keys) or none"
    )


class SetPrimaryRequest(BaseModel):
    person: str = Field(..., description="Person/user ID")
    area: str = Field(..., description="Storage area name")


# --- Routes ---

@router.get("/api/storage/disks")
async def list_disks(request: Request):
    """List all block devices with metadata and safety classification.

    Returns JSON array of disks with:
    - path, by_id_path, name, make, model, serial, size_bytes, size_human
    - transport, rotational, read_only
    - partitions (with fstype, mountpoint, etc.)
    - mountpoints, fstype, uuid, label, parttable_type
    - safety_class: "available" or "protected"
    - safety_reason: comma-separated reasons if protected

    Only disks with safety_class="available" may be formatted.
    """
    # Require admin for disk discovery (exposes hardware topology)
    require_admin(request)

    disks = await discover_disks()

    # Resolve by-id paths
    by_id_paths = {}
    for disk in disks:
        by_id_paths[disk.path] = await _get_disk_by_id_path(disk.path)

    return disks_to_json(disks, by_id_paths=by_id_paths)


@router.post("/api/storage/add")
async def add_storage(request: Request, body: DiskAddRequest):
    """Add a disk as an encrypted storage area.

    Requirements:
    - Caller must be admin
    - Disk must be safety_class="available" at time of operation (re-checked)
    - confirm string must exactly match "<model> <serial>"
    - Area name must be unique and filesystem-safe

    Operations performed:
    1. Create GPT partition table with single partition
    2. LUKS2 format with random key file in /etc/taos/keys/ (if encrypt=system-unlock)
    3. Create ext4 filesystem with label
    4. Write /etc/crypttab entry with key file and nofail
    5. Create systemd mount unit at /srv/taos/storage/<name> with nofail
    6. Mount filesystem
    7. Register in storage registry

    Returns the created storage area on success.
    """
    require_admin(request)

    result = await add_storage_area(
        disk_by_id_path=body.disk,
        confirm_string=body.confirm,
        area_name=body.name,
        encrypt=body.encrypt,
    )

    if not result["ok"]:
        # Map errors to appropriate HTTP status
        error = result.get("error", "Unknown error")
        if "not found" in error.lower():
            raise HTTPException(status_code=404, detail=error)
        elif "confirm" in error.lower() or "match" in error.lower():
            raise HTTPException(status_code=400, detail=error)
        elif "not available" in error.lower() or "safety" in error.lower():
            raise HTTPException(status_code=409, detail=error)
        else:
            raise HTTPException(status_code=500, detail=error)

    # Persist to config
    config = request.app.state.config
    if not hasattr(config, "storage_areas") or config.storage_areas is None:
        config.storage_areas = []
    config.storage_areas.append(result["area"])

    from tinyagentos.config import save_config_locked
    if config.config_path and config.config_path.exists():
        await save_config_locked(config, config.config_path)

    return result["area"]


@router.post("/api/storage/set-primary")
async def set_primary(request: Request, body: SetPrimaryRequest):
    """Set a storage area as a person's primary home.

    Creates the person's home directory at /srv/taos/storage/<area>/homes/<person_id>
    with correct permissions. Records the assignment in config.
    Does NOT move existing files (that happens on user request via separate flow).

    Requires admin or the person themselves.
    """
    user = current_user(request)
    # Allow admin or the person themselves
    if not user.is_admin and user.user_id != body.person:
        raise HTTPException(status_code=403, detail="forbidden")

    config = request.app.state.config
    result = await set_primary_home(body.person, body.area, config)

    if not result["ok"]:
        error = result.get("error", "Unknown error")
        if "not found" in error.lower():
            raise HTTPException(status_code=404, detail=error)
        raise HTTPException(status_code=500, detail=error)

    # Persist config
    from tinyagentos.config import save_config_locked
    if config.config_path and config.config_path.exists():
        await save_config_locked(config, config.config_path)

    return result


@router.get("/api/storage/areas")
async def list_areas(request: Request):
    """List all registered storage areas with current capacity."""
    require_admin(request)

    config = request.app.state.config
    areas = await list_storage_areas(config)
    return [a.__dict__ for a in areas]


@router.get("/api/storage/primary/{person_id}")
async def get_primary(request: Request, person_id: str):
    """Get capacity info for a person's primary home.

    Used by phones and other devices to show primary home capacity/free space.
    Accessible by the person themselves or admin.
    """
    user = current_user(request)
    if not user.is_admin and user.user_id != person_id:
        raise HTTPException(status_code=403, detail="forbidden")

    config = request.app.state.config
    result = await get_primary_home_capacity(person_id, config)

    if result is None:
        raise HTTPException(status_code=404, detail="No primary home set for this person")

    return result