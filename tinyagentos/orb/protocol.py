"""BEGIN/BEGIN_APP/DATA payload builders and INFO parsing."""
import hashlib
import struct

from .frames import CHUNK, OrbError, T_DATA, cstr, encode_frame, from_cstr
from .image import APPID_LEN, LABEL_LEN, NAME_LEN

ADD_FLAG = 0x80
INFO_FIXED = 126                     # the s4 part of an INFO record; app_id, pinned, blocks follow (rec_len says how far)


def begin_payload(slot, image, display_name, sha256):
    """Classic 0x02 BEGIN: slot 0 (taOS) only."""
    return struct.pack("<BI", slot, len(image)) + sha256 + cstr(display_name, NAME_LEN)


class Extent:
    """One extent of an app install. blob None = nothing to send: a blank data extent, or kept data carried over an update."""

    def __init__(self, label, type_, subtype, nblocks, keep, blob):
        self.label, self.type, self.subtype, self.nblocks, self.keep, self.blob = label, type_, subtype, nblocks, keep, blob


def begin_app_payload(app_id, display, exts, add=False):
    """0x07 BEGIN_APP: app_id[32] display_name[24] n_ext(bit 7 = ADD) then per extent label[16] type subtype nblocks keep
    u32 total sha256[32] (total 0 and a zero hash for an extent without data)."""
    if not 1 <= len(exts) <= 4:
        raise ValueError("an app has 1..4 extents")
    p = cstr(app_id, APPID_LEN) + cstr(display, NAME_LEN) + bytes([len(exts) | (ADD_FLAG if add else 0)])
    for x in exts:
        p += cstr(x.label, LABEL_LEN) + bytes([x.type, x.subtype, x.nblocks, x.keep])
        if x.blob:
            p += struct.pack("<I", len(x.blob)) + hashlib.sha256(x.blob).digest()
        else:
            p += struct.pack("<I", 0) + b"\0" * 32
    return p


def data_frames_ext(ext, blob, chunk=CHUNK):
    """0x03 DATA of a BEGIN_APP session: u8 ext, u32 offset, bytes."""
    return [encode_frame(T_DATA, struct.pack("<BI", ext, off) + blob[off:off + chunk]) for off in range(0, len(blob), chunk)]


def parse_info(payload):
    """INFO v2 payload -> {"slots": [...], "free": n, "total": n}. Layout: u8 count, u16 rec_len, u8 free, u8 total, then
    count records of rec_len bytes (a longer record than we know: the tail is skipped). Record: u8 index (0xFF = slot 0),
    u32 size, u8 present, project_name[32], version[32], elf_sha[32], display_name[24], app_id[32], u8 pinned, u8 blocks."""
    if len(payload) < 5:
        raise OrbError("INFO too short")
    count, rec_len, free, total = struct.unpack_from("<BHBB", payload, 0)
    if rec_len < INFO_FIXED or len(payload) != 5 + count * rec_len:
        raise OrbError("INFO has a bad length")
    slots = []
    for i in range(count):
        p = payload[5 + i * rec_len:5 + (i + 1) * rec_len]
        idx, size, present = struct.unpack_from("<BIB", p, 0)
        s = {
            "index": idx, "size": size, "present": bool(present),
            "project_name": from_cstr(p[6:38]), "version": from_cstr(p[38:70]),
            "elf_sha256": bytes(p[70:102]), "display_name": from_cstr(p[102:126]),
            "app_id": "", "pinned": False, "blocks": 0,
        }
        if rec_len >= 160:
            s["app_id"] = from_cstr(p[126:158])
            s["pinned"] = bool(p[158])
            s["blocks"] = p[159]
        slots.append(s)
    return {"slots": slots, "free": free, "total": total}

