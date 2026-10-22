"""App image parsing and local pre-checks."""
import struct

from .frames import OrbError, from_cstr

DESC_OFF = 32
DESC_MAGIC = 0xABCD5432
DESC_END = DESC_OFF + 256
NAME_LEN = 24
APPID_LEN = 32
LABEL_LEN = 16
NAME_TAOS = "taos-orb"
NAME_LAUNCHER = "taos-orb-launcher"
TAOS_INDEX = 0xFF
BLOCK = 0x80000


# ------------------------------------------------------------------ image ----
def parse_image(data):
    """Parse an app image: 0xE9 header, segment_count > 0, esp_app_desc_t with its magic at offset 32.
    Returns dict(project_name, version, elf_sha256).  Raises OrbError for anything the device would call BAD_IMAGE."""
    if len(data) < DESC_END:
        raise OrbError("image too short (%d bytes) to hold an app descriptor" % len(data))
    if data[0] != 0xE9:
        raise OrbError("not an app image: first byte is 0x%02x, expected 0xE9" % data[0])
    if data[1] == 0:
        raise OrbError("image header says 0 segments")
    (magic,) = struct.unpack_from("<I", data, DESC_OFF)
    if magic != DESC_MAGIC:
        raise OrbError("no app descriptor at offset 32 (0x%08x): this looks like a full-chip image or a bootloader, "
                       "not an app .bin" % magic)
    # esp_app_desc_t: magic u32, secure_version u32, reserv1[2], version[32], project_name[32], time[16], date[16],
    # idf_ver[32], app_elf_sha256[32]
    d = data[DESC_OFF:DESC_OFF + 256]
    return {"version": from_cstr(d[16:48]), "project_name": from_cstr(d[48:80]), "elf_sha256": bytes(d[144:176])}


def check_image(image, kind):
    """Refuse locally what the device would refuse. kind: "app" (a pool entry) or "taos" (slot 0)."""
    desc = parse_image(image)
    name = desc["project_name"]
    if kind == "taos" and name != NAME_TAOS:
        raise OrbError("slot 0 is taOS only: image project_name is %r, expected %r" % (name, NAME_TAOS))
    if kind == "app" and name in (NAME_TAOS, NAME_LAUNCHER):
        raise OrbError("%r (taos / launcher) cannot be installed as an app" % name)
    return desc

