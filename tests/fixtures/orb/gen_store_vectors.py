#!/usr/bin/env python3
"""Generate the shared Orb Store test vectors: test/vectors/orb_store_*.bin.

Both the C host test (test/host/test_orb_store.c) and the Python tests (tools/test_orb_store.py) read these files.
This script has its own tiny encoder (struct + zlib.crc32) on purpose: it is the independent reference the tool is
checked against.  Everything is deterministic, rerunning rewrites byte-identical files.

  orb_store_img_<name>.bin   small fake app images (real 24 B header + 8 B segment header + esp_app_desc_t at 32)
  orb_store_frames.bin       records: u8 name_len, name, u32 len (LE), frame bytes
Usage: tools/gen_store_vectors.py [outdir]   (default test/vectors next to this script's repo root)
"""
import hashlib
import os
import struct
import sys
import zlib

DESC_MAGIC = 0xABCD5432


def prng(seed, n):
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(("%s:%d" % (seed, i)).encode()).digest()
        i += 1
    return bytes(out[:n])


def cstr(s, n):
    b = s.encode()
    assert len(b) < n
    return b + b"\0" * (n - len(b))


def make_image(project, version, elf_seed, size, segs=3):
    """Fake app image: 0xE9 header (24 B), first segment header (8 B), esp_app_desc_t at offset 32."""
    img = bytearray(prng("body:%s:%s:%d" % (project, version, size), size))
    img[0] = 0xE9
    img[1] = segs
    img[2], img[3] = 2, 0x40
    struct.pack_into("<I", img, 4, 0x40380000)       # entry
    img[8:24] = b"\0" * 16
    struct.pack_into("<II", img, 24, 0x3C020020, size - 32)
    desc = struct.pack("<II8s", DESC_MAGIC, 0, b"\0" * 8)
    desc += cstr(version, 32) + cstr(project, 32) + cstr("12:00:00", 16) + cstr("Oct  5 2026", 16)
    desc += cstr("v5.5.1", 32) + hashlib.sha256(("elf:" + elf_seed).encode()).digest()
    desc += b"\0" * (256 - len(desc))
    img[32:32 + 256] = desc
    return bytes(img)


def make_fullchip(size):
    """A full 'flash dump': bootloader at 0 (0xE9 but no desc magic at 32) and a partition table magic later."""
    img = bytearray(prng("fullchip", size))
    img[0] = 0xE9
    img[1] = 3
    struct.pack_into("<I", img, 32, 0x00000000)
    img[0x2000:0x2002] = b"\xAA\x50"
    return bytes(img)


def frame(ftype, payload=b""):
    body = struct.pack("<BH", ftype, len(payload)) + payload
    return b"ORBS" + body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def begin_payload(slot, image, display):
    """Legacy 0x02 BEGIN: slot 0 (taOS) only in the block layout."""
    return struct.pack("<BI", slot, len(image)) + hashlib.sha256(image).digest() + cstr(display, 24)


def data_frames(image, chunk=4096):
    """Legacy 0x03 DATA (no ext byte): u32 offset + bytes."""
    return [frame(0x03, struct.pack("<I", off) + image[off:off + chunk]) for off in range(0, len(image), chunk)]


def data_frames_ext(ext, blob, chunk=4096):
    """0x03 DATA of a BEGIN_APP session: u8 ext, u32 offset, bytes."""
    return [frame(0x03, struct.pack("<BI", ext, off) + blob[off:off + chunk]) for off in range(0, len(blob), chunk)]


T_BEGIN_APP, T_PIN = 0x07, 0x08
ADD_FLAG = 0x80


def app_ext(label, typ, subtype, nblocks, keep, blob):
    """One extent of a BEGIN_APP: blob None = blank extent or a carried keep extent (total 0)."""
    return {"label": label, "type": typ, "subtype": subtype, "nblocks": nblocks, "keep": keep, "blob": blob}


def begin_app_payload(app_id, display, exts, add=False):
    p = cstr(app_id, 32) + cstr(display, 24) + bytes([len(exts) | (ADD_FLAG if add else 0)])
    for x in exts:
        blob = x["blob"]
        p += cstr(x["label"], 16) + bytes([x["type"], x["subtype"], x["nblocks"], x["keep"]])
        p += struct.pack("<I", len(blob) if blob else 0) + (hashlib.sha256(blob).digest() if blob else b"\0" * 32)
    return p


INFO_REC_LEN = 160


def info_payload(free, total, recs):
    """INFO v2: u8 count, u16 rec_len, u8 free, u8 total, then rec_len bytes per record."""
    p = struct.pack("<BHBB", len(recs), INFO_REC_LEN, free, total)
    for (idx, size, present, proj, ver, sha, disp, app_id, pinned, blocks) in recs:
        r = struct.pack("<BIB", idx, size, present) + cstr(proj, 32) + cstr(ver, 32) + sha + cstr(disp, 24)
        r += cstr(app_id, 32) + bytes([pinned, blocks])
        assert len(r) == INFO_REC_LEN
        p += r
    return p


# ---------------------------------------------------------------- orb_card map (SPEC-orb-store-blocks s2) ----
CARD_MAGIC = b"OCRD"
CARD_HDR_LEN = 18
CARD_MAX_ENTRIES = 12
CARD_ENTRY_LEN = 177
CARD_MAP_LEN = CARD_HDR_LEN + CARD_MAX_ENTRIES * CARD_ENTRY_LEN + 4
CARD_EMPTY, CARD_READY, CARD_INSTALLING, CARD_MOVING = 0, 1, 2, 3
CARD_TYPE_APP, CARD_TYPE_DATA, CARD_SUBTYPE_OTA15 = 0, 1, 0x1F


def card_ext(label, first, nblocks, typ=CARD_TYPE_DATA, subtype=0x82, keep=0):
    return {"label": label, "type": typ, "subtype": subtype, "first": first, "nblocks": nblocks, "keep": keep}


def card_app_ext(first, nblocks):
    return card_ext("orb_app", first, nblocks, CARD_TYPE_APP, CARD_SUBTYPE_OTA15, 0)


def card_entry(state, app_id, name, elf_seed, exts, pinned=0, move_ext=0, move_dst=0):
    """Independent reference encoder of one 177-byte map entry."""
    e = bytes([state]) + cstr(app_id, 32) + cstr(name, 24) + hashlib.sha256(("elf:" + elf_seed).encode()).digest()
    e += bytes([pinned, len(exts)])
    for i in range(4):
        x = exts[i] if i < len(exts) else card_ext("", 0, 0, 0, 0, 0)
        e += cstr(x["label"], 16) + bytes([x["type"], x["subtype"], x["first"], x["nblocks"], x["keep"]])
    e += bytes([move_ext, move_dst])
    assert len(e) == CARD_ENTRY_LEN
    return e


def card_empty_entry():
    return b"\0" * CARD_ENTRY_LEN


def card_map(seq, entries, block_size=0x80000, nblocks=23):
    ents = list(entries) + [card_empty_entry()] * (CARD_MAX_ENTRIES - len(entries))
    body = CARD_MAGIC + struct.pack("<HHIIBB", 1, CARD_HDR_LEN, seq, block_size, nblocks, CARD_MAX_ENTRIES) + b"".join(ents)
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def card_vector_map():
    """The map used by the C decode test and by the QEMU merged image generator (seq 7, four entries)."""
    return card_map(7, [
        card_entry(CARD_READY, "openharness-taos", "OpenHarness", "oh-fork", [card_app_ext(0, 7)]),
        card_entry(CARD_READY, "xiaozhi", "Xiaozhi", "xiaozhi", [card_app_ext(7, 3), card_ext("assets", 10, 4, keep=1)], pinned=1),
        card_entry(CARD_MOVING, "mimiclaw", "MimiClaw", "mimiclaw", [card_app_ext(14, 2), card_ext("spiffs", 16, 2, keep=1)],
                   move_ext=1, move_dst=20),
        card_entry(CARD_INSTALLING, "zclaw", "zclaw", "zclaw", [card_app_ext(18, 2)]),
    ])


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[1] if len(sys.argv) > 1 else here
    os.makedirs(out, exist_ok=True)
    images = {
        "taos": make_image("taos-orb", "1.0.0", "taos", 6000),
        "guest_a": make_image("interns_commander", "0.0.86", "oh-stock", 9000),
        "guest_b": make_image("hello_guest", "1.0", "hello", 5000),
        # same elf sha as guest_a, different version and bytes: the dedupe must key on the elf sha only
        "dup_a": make_image("interns_commander", "0.0.86", "oh-stock", 9100),
        # different elf sha, SAME project name and version as guest_a: must NOT be a duplicate
        "samename_a": make_image("interns_commander", "0.0.86", "oh-fork", 9000),
        # different project, same elf sha as the taos image: a guest slot must refuse it as a duplicate of slot 0
        "dup_taos": make_image("hello_guest", "1.0", "taos", 5000),
        "launcher": make_image("taos-orb-launcher", "0.3.0", "launcher", 6000),
        # a newer build of guest_b / guest_a under the same app id (update tests): other elf sha, other version
        "guest_a2": make_image("interns_commander", "0.0.87", "oh-stock-2", 9500),
        "guest_b2": make_image("hello_guest", "1.1", "hello2", 5200),
        "noseg": make_image("hello_guest", "1.0", "noseg", 5000, segs=0),
        "fullchip": make_fullchip(12000),
    }
    bad = bytearray(make_image("hello_guest", "1.0", "badhdr", 5000))
    bad[0] = 0x00
    images["badhdr"] = bytes(bad)
    for name, img in images.items():
        with open(os.path.join(out, "orb_store_img_%s.bin" % name), "wb") as f:
            f.write(img)

    for name, blob in (("assets", prng("data:assets", 6000)), ("assets2", prng("data:assets2", 6500))):
        with open(os.path.join(out, "orb_store_data_%s.bin" % name), "wb") as f:
            f.write(blob)
    assets = prng("data:assets", 6000)
    a, t, gb = images["guest_a"], images["taos"], images["guest_b"]
    rec = []
    rec.append(("hello", frame(0x01)))
    rec.append(("begin_taos", frame(0x02, begin_payload(0, t, "taOS"))))
    for i, fr in enumerate(data_frames(t)):
        rec.append(("data_taos_%d" % i, fr))
    rec.append(("end", frame(0x04)))
    rec.append(("remove_1", frame(0x05, bytes([1]))))
    rec.append(("remove_taos", frame(0x05, bytes([0xFF]))))
    rec.append(("pin_1_on", frame(T_PIN, bytes([1, 1]))))
    rec.append(("pin_1_off", frame(T_PIN, bytes([1, 0]))))
    rec.append(("abort", frame(0x06)))
    bad_crc = bytearray(frame(0x02, begin_payload(0, t, "taOS")))
    bad_crc[-1] ^= 0x01
    rec.append(("begin_taos_badcrc", bytes(bad_crc)))
    # xiaozhi: an app extent + a kept data extent ("assets", 1 block each)
    xz = [app_ext("orb_app", 0, 0x1F, 1, 0, gb), app_ext("assets", 1, 0x82, 1, 1, assets)]
    rec.append(("begin_app_xz", frame(T_BEGIN_APP, begin_app_payload("xiaozhi", "Xiaozhi", xz))))
    for i, fr in enumerate(data_frames_ext(0, gb)):
        rec.append(("data_xz_e0_%d" % i, fr))
    for i, fr in enumerate(data_frames_ext(1, assets)):
        rec.append(("data_xz_e1_%d" % i, fr))
    # the update of xiaozhi: new app image, the kept assets extent is carried (total 0, not resent)
    xz2 = [app_ext("orb_app", 0, 0x1F, 1, 0, images["guest_b2"]), app_ext("assets", 1, 0x82, 1, 1, None)]
    rec.append(("begin_app_xz_update", frame(T_BEGIN_APP, begin_app_payload("xiaozhi", "Xiaozhi", xz2))))
    for i, fr in enumerate(data_frames_ext(0, images["guest_b2"])):
        rec.append(("data_xz2_e0_%d" % i, fr))
    # openharness: one extent, installed side by side (ADD flag) with another build of the same app id
    oh = [app_ext("orb_app", 0, 0x1F, 1, 0, a)]
    rec.append(("begin_app_oh", frame(T_BEGIN_APP, begin_app_payload("openharness-taos", "OpenHarness", oh))))
    rec.append(("begin_app_oh_add", frame(T_BEGIN_APP, begin_app_payload("openharness-taos", "OpenHarness", oh, add=True))))
    for i, fr in enumerate(data_frames_ext(0, a)):
        rec.append(("data_oh_e0_%d" % i, fr))
    rec.append(("ack_data_4096", frame(0x80, struct.pack("<BI", 0x03, 4096))))
    rec.append(("nak_duplicate_end", frame(0x7F, bytes([0x04, 7]))))
    rec.append(("nak_no_space_begin_app", frame(0x7F, bytes([T_BEGIN_APP, 12]))))
    sha_a = hashlib.sha256(("elf:oh-stock").encode()).digest()
    rec.append(("info_example", frame(0x81, info_payload(19, 23, [
        (0xFF, 0x300000, 1, "taos-orb", "1.0.0", hashlib.sha256(b"elf:taos").digest(), "taOS", "", 0, 0),
        (0, 0x80000, 1, "interns_commander", "0.0.86", sha_a, "OpenHarness", "openharness-taos", 0, 1),
        (3, 0x100000, 1, "hello_guest", "1.0", hashlib.sha256(b"elf:hello").digest(), "Xiaozhi", "xiaozhi", 1, 3),
    ]))))
    rec.append(("progress_example", frame(0x82, struct.pack("<II", 4096, 9000))))
    with open(os.path.join(out, "orb_card_map_four.bin"), "wb") as f:
        f.write(card_vector_map())
    with open(os.path.join(out, "orb_store_frames.bin"), "wb") as f:
        for name, fr in rec:
            f.write(bytes([len(name)]) + name.encode() + struct.pack("<I", len(fr)) + fr)


if __name__ == "__main__":
    main()
