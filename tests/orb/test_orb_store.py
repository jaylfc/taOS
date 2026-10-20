#!/usr/bin/env python3
"""Tests for tinyagentos/orb against the shared vectors (test/vectors/orb_store_*.bin, also read by the C tests):
framing of every frame including BEGIN_APP with extents, INFO v2, the catalog rules of SPEC-orb-store.md s11
(latest = first non-yanked, yanked never default, downgrade needs --version, update skips pinned, sha256 mismatch refused
before BEGIN, elf-sha identifies the installed version) and the client flows against a scripted serial port.
   pytest tests/orb -q      (no extra installs; pyserial only for the real port)
"""
import contextlib
import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
from tinyagentos import orb as ob

VEC = os.path.join(HERE, "..", "fixtures", "orb")


def img(name):
    with open(os.path.join(VEC, "orb_store_img_%s.bin" % name), "rb") as f:
        return f.read()


def dat(name):
    with open(os.path.join(VEC, "orb_store_data_%s.bin" % name), "rb") as f:
        return f.read()


def frames():
    out = {}
    with open(os.path.join(VEC, "orb_store_frames.bin"), "rb") as f:
        d = f.read()
    p = 0
    while p < len(d):
        nl = d[p]
        name = d[p + 1:p + 1 + nl].decode()
        p += 1 + nl
        (ln,) = struct.unpack_from("<I", d, p)
        out[name] = d[p + 4:p + 4 + ln]
        p += 4 + ln
    return out


FR = frames()
ZERO = b"\0" * 32


def sha(b):
    return hashlib.sha256(b).hexdigest()


def elf(b):
    return ob.parse_image(b)["elf_sha256"].hex()


def row(index, **kw):
    r = {"index": index, "size": 0x80000, "present": True, "project_name": "", "version": "", "elf_sha256": ZERO,
         "display_name": "", "app_id": "", "pinned": False, "blocks": 1}
    r.update(kw)
    return r


def info(rows, free=20, total=23):
    return {"slots": rows, "free": free, "total": total}


TAOS = row(0xFF, size=0x300000, project_name="taos-orb", version="1.0.0", display_name="taOS", blocks=0,
           elf_sha256=ob.parse_image(img("taos"))["elf_sha256"])


def entry_for(index, image, app_id, **kw):
    d = ob.parse_image(image)
    return row(index, project_name=d["project_name"], version=d["version"], elf_sha256=d["elf_sha256"], app_id=app_id,
               display_name=app_id, **kw)


# ---------------------------------------------------------------- the catalog under test ----
def write_catalog(tmp):
    """xiaozhi 1.1 / 1.0 (assets kept across versions), a stock and a fork of openharness that BOTH report
    interns_commander 0.0.86, a yanked newest version of 'zclaw'."""
    b, b2, a, a2, same = img("guest_b"), img("guest_b2"), img("guest_a"), img("guest_a2"), img("samename_a")
    assets = dat("assets")

    def ver(version, image, exts, yanked=False, notes=""):
        fn = os.path.join(VEC, "orb_store_img_%s.bin" % image)
        blob = img(image)
        return {"version": version, "file": fn, "sha256": sha(blob), "elf_sha256": elf(blob),
                "size": os.path.getsize(fn), "released": "2026-10-09", "notes": notes, "yanked": yanked,
                "blocks": sum(e["blocks"] for e in exts), "extents": exts}

    def app_ext(fn):
        return {"label": "orb_app", "type": 0, "subtype": 0x1F, "blocks": 1, "keep": 0, "file": fn}

    def data_ext(file_name):
        return {"label": "assets", "type": 1, "subtype": 0x82, "blocks": 1, "keep": 1, "file": file_name}

    assets_fn = os.path.join(VEC, "orb_store_data_assets.bin")
    cat = {"apps": [
        {"id": "xiaozhi", "name": "Xiaozhi", "versions": [
            ver("1.1", "guest_b2", [app_ext(os.path.join(VEC, "orb_store_img_guest_b2.bin")), data_ext(None)], notes="newer"),
            ver("1.0", "guest_b", [app_ext(os.path.join(VEC, "orb_store_img_guest_b.bin")), data_ext(assets_fn)], notes="first")]},
        {"id": "openharness-stock", "name": "OpenHarness", "versions": [
            ver("0.0.86", "guest_a", [app_ext(os.path.join(VEC, "orb_store_img_guest_a.bin"))])]},
        {"id": "openharness-taos", "name": "OpenHarness taOS", "versions": [
            ver("0.0.86", "samename_a", [app_ext(os.path.join(VEC, "orb_store_img_samename_a.bin"))])]},
        {"id": "xz2", "name": "Second", "versions": [
            ver("2.0", "guest_a2", [app_ext(os.path.join(VEC, "orb_store_img_guest_a2.bin"))]),
            ver("1.0", "guest_a", [app_ext(os.path.join(VEC, "orb_store_img_guest_a.bin"))])]},
        {"id": "zclaw", "name": "zclaw", "versions": [
            ver("2.0", "guest_a2", [app_ext(os.path.join(VEC, "orb_store_img_guest_a2.bin"))], yanked=True, notes="pulled"),
            ver("1.0", "guest_a", [app_ext(os.path.join(VEC, "orb_store_img_guest_a.bin"))])]},
    ]}
    path = os.path.join(tmp, "catalog.json")
    with open(path, "w") as f:
        json.dump(cat, f)
    return path


class Framing(unittest.TestCase):
    def test_encode_matches_vectors(self):
        t = img("taos")
        self.assertEqual(ob.encode_frame(ob.T_HELLO), FR["hello"])
        self.assertEqual(ob.encode_frame(ob.T_BEGIN, ob.begin_payload(0, t, "taOS", hashlib.sha256(t).digest())), FR["begin_taos"])
        for i, off in enumerate(range(0, len(t), 4096)):
            self.assertEqual(ob.encode_frame(ob.T_DATA, struct.pack("<I", off) + t[off:off + 4096]), FR["data_taos_%d" % i])
        self.assertEqual(ob.encode_frame(ob.T_END), FR["end"])
        self.assertEqual(ob.encode_frame(ob.T_REMOVE, bytes([1])), FR["remove_1"])
        self.assertEqual(ob.encode_frame(ob.T_REMOVE, bytes([ob.TAOS_INDEX])), FR["remove_taos"])
        self.assertEqual(ob.encode_frame(ob.T_PIN, bytes([1, 1])), FR["pin_1_on"])
        self.assertEqual(ob.encode_frame(ob.T_PIN, bytes([1, 0])), FR["pin_1_off"])
        self.assertEqual(ob.encode_frame(ob.T_ABORT), FR["abort"])
        self.assertEqual(ob.encode_frame(ob.T_ACK, struct.pack("<BI", 3, 4096)), FR["ack_data_4096"])
        self.assertEqual(ob.encode_frame(ob.T_NAK, bytes([4, 7])), FR["nak_duplicate_end"])
        self.assertEqual(ob.encode_frame(ob.T_NAK, bytes([ob.T_BEGIN_APP, 12])), FR["nak_no_space_begin_app"])
        self.assertEqual(ob.encode_frame(ob.T_PROGRESS, struct.pack("<II", 4096, 9000)), FR["progress_example"])

    def test_frame_type_numbers(self):
        self.assertEqual((ob.T_BEGIN_APP, ob.T_PIN), (0x07, 0x08))
        self.assertEqual(ob.NAK_NAMES[12], "NO_SPACE")

    def test_begin_app_framing_matches_vectors(self):
        b, as_ = img("guest_b"), dat("assets")
        exts = [ob.Extent("orb_app", 0, 0x1F, 1, 0, b), ob.Extent("assets", 1, 0x82, 1, 1, as_)]
        self.assertEqual(ob.encode_frame(ob.T_BEGIN_APP, ob.begin_app_payload("xiaozhi", "Xiaozhi", exts)), FR["begin_app_xz"])
        exts = [ob.Extent("orb_app", 0, 0x1F, 1, 0, img("guest_b2")), ob.Extent("assets", 1, 0x82, 1, 1, None)]
        self.assertEqual(ob.encode_frame(ob.T_BEGIN_APP, ob.begin_app_payload("xiaozhi", "Xiaozhi", exts)), FR["begin_app_xz_update"])
        oh = [ob.Extent("orb_app", 0, 0x1F, 1, 0, img("guest_a"))]
        self.assertEqual(ob.encode_frame(ob.T_BEGIN_APP, ob.begin_app_payload("openharness-taos", "OpenHarness", oh)), FR["begin_app_oh"])
        self.assertEqual(ob.encode_frame(ob.T_BEGIN_APP, ob.begin_app_payload("openharness-taos", "OpenHarness", oh, add=True)), FR["begin_app_oh_add"])

    def test_begin_app_payload_layout(self):
        exts = [ob.Extent("orb_app", 0, 0x1F, 3, 0, b"x" * 300), ob.Extent("blank", 1, 0x82, 2, 1, None)]
        p = ob.begin_app_payload("my-app", "My App", exts)
        self.assertEqual(len(p), 57 + 2 * 56)
        self.assertEqual(p[:32].rstrip(b"\0"), b"my-app")
        self.assertEqual(p[32:56].rstrip(b"\0"), b"My App")
        self.assertEqual(p[56], 2)
        self.assertEqual(p[57:73].rstrip(b"\0"), b"orb_app")
        self.assertEqual(p[73:77], bytes([0, 0x1F, 3, 0]))
        self.assertEqual(struct.unpack_from("<I", p, 77)[0], 300)
        self.assertEqual(p[81:113], hashlib.sha256(b"x" * 300).digest())
        self.assertEqual(struct.unpack_from("<I", p, 57 + 56 + 20)[0], 0)
        self.assertEqual(p[57 + 56 + 24:57 + 56 + 56], ZERO)
        self.assertEqual(ob.begin_app_payload("a", "b", exts, add=True)[56], 0x82)
        with self.assertRaises(ValueError):
            ob.begin_app_payload("x" * 32, "b", exts)

    def test_data_ext_framing_matches_vectors(self):
        b, as_ = img("guest_b"), dat("assets")
        for i, f in enumerate(ob.data_frames_ext(0, b)):
            self.assertEqual(f, FR["data_xz_e0_%d" % i])
        for i, f in enumerate(ob.data_frames_ext(1, as_)):
            self.assertEqual(f, FR["data_xz_e1_%d" % i])
        for i, f in enumerate(ob.data_frames_ext(0, img("guest_b2"))):
            self.assertEqual(f, FR["data_xz2_e0_%d" % i])

    def test_payload_limit(self):
        ob.encode_frame(ob.T_DATA, b"x" * ob.MAX_PAYLOAD)
        with self.assertRaises(ValueError):
            ob.encode_frame(ob.T_DATA, b"x" * (ob.MAX_PAYLOAD + 1))

    def test_decode_vectors(self):
        d = ob.FrameDecoder()
        self.assertEqual(d.feed(FR["hello"]), [(ob.T_HELLO, b"")])
        self.assertEqual(d.feed(FR["ack_data_4096"]), [(ob.T_ACK, struct.pack("<BI", 3, 4096))])
        self.assertEqual(d.feed(FR["nak_duplicate_end"]), [(ob.T_NAK, bytes([4, 7]))])
        self.assertEqual(d.bad_crc, 0)

    def test_resync_past_log_text(self):
        d = ob.FrameDecoder()
        noise = b"I (123) launcher: boot ORB ORBX ORBS\r\nE (5) bad\n"
        out = d.feed(noise + FR["hello"] + b"\xff\x00ORB\x00OR" + FR["end"])
        self.assertEqual([t for t, _ in out], [ob.T_HELLO, ob.T_END])

    def test_byte_at_a_time_and_split_magic(self):
        d = ob.FrameDecoder()
        out = []
        for b in b"xxO" + FR["info_example"][1:]:
            out += d.feed(bytes([b]))
        self.assertEqual([t for t, _ in out], [ob.T_INFO])

    def test_bad_crc_dropped(self):
        d = ob.FrameDecoder()
        out = d.feed(FR["begin_taos_badcrc"] + FR["hello"])
        self.assertEqual([t for t, _ in out], [ob.T_HELLO])
        self.assertEqual(d.bad_crc, 1)

    def test_oversize_len_and_hidden_frame(self):
        d = ob.FrameDecoder()
        out = d.feed(b"ORBS\x03\xff\xff" + FR["hello"])
        self.assertEqual([t for t, _ in out], [ob.T_HELLO])
        d = ob.FrameDecoder()
        out = d.feed(b"ORBS\x01\x0b\x00" + FR["hello"] + b"\0\0\0\0")
        self.assertEqual([t for t, _ in out], [ob.T_HELLO])
        self.assertEqual(d.bad_crc, 1)

    def test_info_parse(self):
        d = ob.FrameDecoder()
        (t, p), = d.feed(FR["info_example"])
        self.assertEqual(t, ob.T_INFO)
        r = ob.parse_info(p)
        self.assertEqual((r["free"], r["total"]), (19, 23))
        slots = r["slots"]
        self.assertEqual([s["index"] for s in slots], [0xFF, 0, 3])
        self.assertEqual(slots[0]["project_name"], "taos-orb")
        self.assertEqual(slots[0]["size"], 0x300000)
        self.assertEqual(slots[1]["display_name"], "OpenHarness")
        self.assertEqual(slots[1]["app_id"], "openharness-taos")
        self.assertEqual(slots[1]["version"], "0.0.86")
        self.assertEqual(slots[1]["elf_sha256"], hashlib.sha256(b"elf:oh-stock").digest())
        self.assertEqual(slots[2]["blocks"], 3)
        self.assertTrue(slots[2]["pinned"])
        self.assertFalse(slots[1]["pinned"])
        with self.assertRaises(ob.OrbError):
            ob.parse_info(p[:-1])
        with self.assertRaises(ob.OrbError):
            ob.parse_info(b"")

    def test_info_rec_len_lets_an_old_parser_skip_unknown_tail_bytes(self):
        d = ob.FrameDecoder()
        (_, p), = d.feed(FR["info_example"])
        count = p[0]
        grown = bytearray(struct.pack("<BHBB", count, 160 + 8, p[3], p[4]))
        for i in range(count):
            grown += p[5 + i * 160:5 + (i + 1) * 160] + b"TAILTAIL"
        r = ob.parse_info(bytes(grown))
        self.assertEqual([s["index"] for s in r["slots"]], [0xFF, 0, 3])
        self.assertEqual(r["slots"][1]["app_id"], "openharness-taos")


class ImageParser(unittest.TestCase):
    def test_good_images(self):
        a = ob.parse_image(img("guest_a"))
        self.assertEqual((a["project_name"], a["version"]), ("interns_commander", "0.0.86"))
        self.assertEqual(a["elf_sha256"], hashlib.sha256(b"elf:oh-stock").digest())
        self.assertEqual(ob.parse_image(img("taos"))["project_name"], "taos-orb")
        self.assertEqual(ob.parse_image(img("launcher"))["project_name"], "taos-orb-launcher")

    def test_desc_layout_offsets(self):
        a = img("guest_a")
        self.assertEqual(a[48:54], b"0.0.86")
        self.assertEqual(a[80:97], b"interns_commander")
        self.assertEqual(a[176:208], hashlib.sha256(b"elf:oh-stock").digest())

    def test_refusals(self):
        with self.assertRaisesRegex(ob.OrbError, "full-chip"):
            ob.parse_image(img("fullchip"))
        with self.assertRaisesRegex(ob.OrbError, "0xE9"):
            ob.parse_image(img("badhdr"))
        with self.assertRaisesRegex(ob.OrbError, "segments"):
            ob.parse_image(img("noseg"))
        with self.assertRaisesRegex(ob.OrbError, "too short"):
            ob.parse_image(img("guest_a")[:200])


class Catalog(unittest.TestCase):
    """SPEC-orb-store.md s11.1 and s11.3 rules, no device involved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = write_catalog(self.tmp.name)
        self.cat = ob.load_catalog(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_latest_is_first_non_yanked_in_list_order(self):
        self.assertEqual(ob.latest_version(self.cat.app("xiaozhi"))["version"], "1.1")
        self.assertEqual(ob.latest_version(self.cat.app("zclaw"))["version"], "1.0")      # 2.0 is yanked: never the default

    def test_no_semver_ordering(self):
        app = {"id": "x", "name": "x", "versions": [{"version": "0.0.86-taos.4", "yanked": False}, {"version": "0.0.86-nixfred.11", "yanked": False}]}
        self.assertEqual(ob.latest_version(app)["version"], "0.0.86-taos.4")                 # list order, not version order

    def test_all_yanked_has_no_latest(self):
        app = {"id": "x", "name": "x", "versions": [{"version": "1", "yanked": True}]}
        self.assertIsNone(ob.latest_version(app))

    def test_pick_version_default_and_exact(self):
        self.assertEqual(ob.pick_version(self.cat.app("zclaw"), None)["version"], "1.0")
        self.assertEqual(ob.pick_version(self.cat.app("zclaw"), "2.0")["version"], "2.0")    # a yanked one only by exact --version
        with self.assertRaisesRegex(ob.OrbError, "no version"):
            ob.pick_version(self.cat.app("zclaw"), "9.9")

    def test_unknown_app(self):
        with self.assertRaisesRegex(ob.OrbError, "not in the catalog"):
            self.cat.app("nope")

    def test_blocks_and_extents_loaded(self):
        v = ob.pick_version(self.cat.app("xiaozhi"), "1.0")
        self.assertEqual(v["blocks"], 2)
        self.assertEqual([e["label"] for e in v["extents"]], ["orb_app", "assets"])
        self.assertEqual(v["extents"][1]["keep"], 1)

    def test_extents_derived_for_a_plain_version(self):
        app = {"id": "x", "name": "x", "versions": [{"version": "1", "file": "x.bin", "size": 600000}]}
        exts = ob.version_extents(app["versions"][0])
        self.assertEqual(exts[0]["label"], "orb_app")
        self.assertEqual((exts[0]["type"], exts[0]["subtype"], exts[0]["blocks"], exts[0]["file"]), (0, 0x1F, 2, "x.bin"))

    def test_identify_by_elf_not_by_version_string(self):
        stock, fork = img("guest_a"), img("samename_a")
        self.assertEqual(ob.parse_image(stock)["version"], ob.parse_image(fork)["version"])
        self.assertEqual(ob.parse_image(stock)["project_name"], ob.parse_image(fork)["project_name"])
        a, v = ob.identify(self.cat, ob.parse_image(stock)["elf_sha256"])
        self.assertEqual(a["id"], "openharness-stock")
        a, v = ob.identify(self.cat, ob.parse_image(fork)["elf_sha256"])
        self.assertEqual(a["id"], "openharness-taos")
        self.assertIsNone(ob.identify(self.cat, b"\x01" * 32))

    def test_status_latest_update_pinned_newer_unknown(self):
        b, b2 = img("guest_b"), img("guest_b2")
        self.assertEqual(ob.status_of(self.cat, entry_for(0, b2, "xiaozhi")), "latest")
        self.assertEqual(ob.status_of(self.cat, entry_for(0, b, "xiaozhi")), "update available -> 1.1")
        self.assertEqual(ob.status_of(self.cat, entry_for(0, b, "xiaozhi", pinned=True)), "pinned (latest 1.1)")
        self.assertEqual(ob.status_of(self.cat, entry_for(0, b2, "xiaozhi", pinned=True)), "latest")
        # zclaw 2.0 is newer than the catalog's latest (1.0) because 2.0 was yanked: newer than catalog
        self.assertEqual(ob.status_of(self.cat, entry_for(0, img("guest_a2"), "zclaw")), "newer than catalog")
        self.assertEqual(ob.status_of(self.cat, entry_for(0, img("dup_taos"), "q")), "unknown")
        self.assertEqual(ob.status_of(self.cat, row(0, elf_sha256=b"\x07" * 32)), "unknown")

    def test_status_identifies_stock_and_fork_apart(self):
        stock, fork = img("guest_a"), img("samename_a")
        self.assertEqual(ob.status_of(self.cat, entry_for(0, stock, "")), "latest")
        self.assertEqual(ob.status_of(self.cat, entry_for(1, fork, "")), "latest")

    def test_plan_install_latest_is_the_default(self):
        plan = ob.plan_install(self.cat, info([TAOS]), "xiaozhi", None)
        self.assertEqual(plan["version"]["version"], "1.1")
        self.assertFalse(plan["downgrade"])

    def test_downgrade_needs_an_explicit_version(self):
        b2 = img("guest_b2")
        dev = info([TAOS, entry_for(0, b2, "xiaozhi")])
        with self.assertRaisesRegex(ob.OrbError, "already installed"):
            ob.plan_install(self.cat, dev, "xiaozhi", None)
        plan = ob.plan_install(self.cat, dev, "xiaozhi", "1.0")
        self.assertTrue(plan["downgrade"])
        self.assertEqual(plan["message"], "downgrade 1.1 -> 1.0")

    def test_newer_than_catalog_needs_an_explicit_version(self):
        dev = info([TAOS, entry_for(0, img("guest_a2"), "zclaw")])
        with self.assertRaisesRegex(ob.OrbError, "newer than the catalog"):
            ob.plan_install(self.cat, dev, "zclaw", None)

    def test_upgrade_is_not_a_downgrade(self):
        dev = info([TAOS, entry_for(0, img("guest_b"), "xiaozhi")])
        plan = ob.plan_install(self.cat, dev, "xiaozhi", None)
        self.assertEqual(plan["version"]["version"], "1.1")
        self.assertFalse(plan["downgrade"])
        self.assertTrue(plan["update"])

    def test_same_build_already_installed_is_refused(self):
        dev = info([TAOS, entry_for(2, img("guest_b2"), "xiaozhi")])
        with self.assertRaisesRegex(ob.OrbError, "already installed"):
            ob.plan_install(self.cat, dev, "xiaozhi", "1.1")

    def test_elf_installed_under_another_app_is_refused(self):
        dev = info([TAOS, entry_for(4, img("guest_a"), "something-else")])
        with self.assertRaisesRegex(ob.OrbError, "already installed in entry 4"):
            ob.plan_install(self.cat, dev, "openharness-stock", None)

    def test_slot0_elf_is_refused(self):
        taos_elf = ob.parse_image(img("taos"))["elf_sha256"]
        dev = info([row(0xFF, size=0x300000, project_name="taos-orb", elf_sha256=taos_elf)])
        # the catalog has no taos image, so build a one-off app whose elf equals slot 0's
        self.cat.apps["clash"] = {"id": "clash", "name": "clash", "versions": [{"version": "1", "yanked": False, "elf_sha256": taos_elf.hex(), "extents": []}]}
        with self.assertRaisesRegex(ob.OrbError, "slot 0"):
            ob.plan_install(self.cat, dev, "clash", None)

    def test_update_plan_carries_kept_data_only_when_the_layout_matches(self):
        dev = info([TAOS, entry_for(0, img("guest_b"), "xiaozhi")])
        plan = ob.plan_install(self.cat, dev, "xiaozhi", None)
        exts = ob.build_extents(self.cat, plan)
        self.assertIsNone(exts[1].blob)                       # assets: keep + same label/size in 1.0 -> carried, not resent
        self.assertEqual(exts[0].blob, img("guest_b2"))
        plan = ob.plan_install(self.cat, info([TAOS]), "xiaozhi", "1.0")
        exts = ob.build_extents(self.cat, plan)
        self.assertEqual(exts[1].blob, dat("assets"))          # fresh install: the data is sent

    def test_sha256_mismatch_refused_before_any_frame(self):
        plan = ob.plan_install(self.cat, info([TAOS]), "xiaozhi", "1.0")
        plan["version"]["extents"][0]["file"] = os.path.join(VEC, "orb_store_img_guest_a.bin")   # not the file the catalog hashed
        with self.assertRaisesRegex(ob.OrbError, "sha256"):
            ob.build_extents(self.cat, plan)

    def test_elf_mismatch_between_catalog_and_image_refused(self):
        plan = ob.plan_install(self.cat, info([TAOS]), "xiaozhi", "1.0")
        plan["version"]["elf_sha256"] = "00" * 32
        with self.assertRaisesRegex(ob.OrbError, "elf"):
            ob.build_extents(self.cat, plan)

    def test_updates_skip_pinned(self):
        b = img("guest_b")
        dev = info([TAOS, entry_for(0, b, "xiaozhi", pinned=True), entry_for(1, img("guest_a"), "zclaw"), entry_for(2, img("guest_a"), "xz2")])
        todo, skipped = ob.plan_updates(self.cat, dev, None)
        self.assertEqual([t["entry"]["index"] for t in todo], [2])
        self.assertEqual([s["index"] for s in skipped], [0])
        todo, skipped = ob.plan_updates(self.cat, dev, 0)
        self.assertEqual(todo, [])
        self.assertEqual([s["index"] for s in skipped], [0])
        todo, skipped = ob.plan_updates(self.cat, dev, 2)
        self.assertEqual([t["entry"]["index"] for t in todo], [2])
        self.assertEqual(todo[0]["version"]["version"], "2.0")

    def test_updates_never_target_a_shadowed_entry(self):
        """The device replaces the FIRST READY entry with the app_id: with two entries of one app a pinned first entry
        blocks the update of both."""
        b = img("guest_b")
        dev = info([TAOS, entry_for(0, b, "xiaozhi", pinned=True), entry_for(1, b, "xiaozhi")])
        todo, skipped = ob.plan_updates(self.cat, dev, None)
        self.assertEqual(todo, [])
        self.assertEqual([s["index"] for s in skipped], [0, 1])
        dev = info([TAOS, entry_for(0, b, "xiaozhi"), entry_for(1, b, "xiaozhi")])
        todo, skipped = ob.plan_updates(self.cat, dev, None)
        self.assertEqual([t["entry"]["index"] for t in todo], [0])
        self.assertEqual([s["index"] for s in skipped], [1])

    def test_versions_listing(self):
        text = ob.fmt_versions(self.cat.app("zclaw"))
        self.assertIn("2.0", text)
        self.assertIn("yanked", text)
        self.assertIn("pulled", text)
        self.assertEqual(len([l for l in text.splitlines() if "latest" in l and "1.0" in l]), 1)
        self.assertEqual(len([l for l in text.splitlines() if "latest" in l]), 1)

    def test_list_text_shows_blocks_free_and_status(self):
        dev = info([TAOS, entry_for(0, img("guest_b"), "xiaozhi", blocks=2, pinned=True)], free=21)
        text = ob.fmt_list(dev, self.cat)
        self.assertIn("21 of 23 blocks free", text)
        self.assertIn("pinned (latest 1.1)", text)
        self.assertIn("2 blocks", text)
        self.assertIn("slot 0", text)


class LocalChecks(unittest.TestCase):
    def test_image_checks(self):
        ob.check_image(img("guest_a"), "app")
        ob.check_image(img("taos"), "taos")
        with self.assertRaisesRegex(ob.OrbError, "taos"):
            ob.check_image(img("taos"), "app")
        with self.assertRaisesRegex(ob.OrbError, "launcher"):
            ob.check_image(img("launcher"), "app")
        with self.assertRaisesRegex(ob.OrbError, "slot 0"):
            ob.check_image(img("guest_a"), "taos")
        with self.assertRaisesRegex(ob.OrbError, "full-chip"):
            ob.check_image(img("fullchip"), "app")


class FakeSerial:
    """Scripted device: replies to each frame it is written with the next canned reply list."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.written = []
        self.rx = bytearray()
        self.closed = False

    def write(self, b):
        self.written.append(bytes(b))
        r = self.replies.pop(0) if self.replies else []
        for f in r:
            self.rx += b"I (1) noise\r\n" + f

    def read(self, n):
        d = bytes(self.rx[:n])
        del self.rx[:n]
        return d

    def flush(self):
        pass

    def close(self):
        self.closed = True


def ack(for_type, value=0):
    return ob.encode_frame(ob.T_ACK, struct.pack("<BI", for_type, value))


def nak(for_type, code):
    return ob.encode_frame(ob.T_NAK, bytes([for_type, code]))


def info_frame(rows, free=20, total=23):
    p = struct.pack("<BHBB", len(rows), 160, free, total)
    for r in rows:
        rec = struct.pack("<BIB", r["index"], r["size"], 1 if r["present"] else 0)
        rec += r["project_name"].encode().ljust(32, b"\0") + r["version"].encode().ljust(32, b"\0") + r["elf_sha256"]
        rec += r["display_name"].encode().ljust(24, b"\0") + r["app_id"].encode().ljust(32, b"\0") + bytes([1 if r["pinned"] else 0, r["blocks"]])
        p += rec
    return ob.encode_frame(ob.T_INFO, p)


class ClientFlow(unittest.TestCase):
    def test_install_app_sends_the_vector_frames(self):
        b, as_ = img("guest_b"), dat("assets")
        exts = [ob.Extent("orb_app", 0, 0x1F, 1, 0, b), ob.Extent("assets", 1, 0x82, 1, 1, as_)]
        replies = [[ack(ob.T_BEGIN_APP, 0)], [ack(ob.T_DATA, 4096)], [ack(ob.T_DATA, 5000)], [ack(ob.T_DATA, 9096)], [ack(ob.T_DATA, 11000)],
                   [ack(ob.T_END, 11000)]]
        ser = FakeSerial(replies)
        prog = []
        entry = ob.Client(ser).install_app("xiaozhi", "Xiaozhi", exts, progress=lambda d, t: prog.append((d, t)))
        want = [FR["begin_app_xz"], FR["data_xz_e0_0"], FR["data_xz_e0_1"], FR["data_xz_e1_0"], FR["data_xz_e1_1"], FR["end"]]
        self.assertEqual(ser.written, want)
        self.assertEqual(entry, 0)
        self.assertEqual(prog[-1], (len(b) + len(as_), len(b) + len(as_)))

    def test_update_sends_only_the_fresh_extent(self):
        exts = [ob.Extent("orb_app", 0, 0x1F, 1, 0, img("guest_b2")), ob.Extent("assets", 1, 0x82, 1, 1, None)]
        ser = FakeSerial([[ack(ob.T_BEGIN_APP, 1)], [ack(ob.T_DATA, 4096)], [ack(ob.T_DATA, 5200)], [ack(ob.T_END, 5200)]])
        ob.Client(ser).install_app("xiaozhi", "Xiaozhi", exts)
        self.assertEqual(ser.written, [FR["begin_app_xz_update"], FR["data_xz2_e0_0"], FR["data_xz2_e0_1"], FR["end"]])

    def test_nak_raises_and_aborts(self):
        exts = [ob.Extent("orb_app", 0, 0x1F, 1, 0, img("guest_a"))]
        ser = FakeSerial([[ack(ob.T_BEGIN_APP, 0)], [ack(ob.T_DATA, 4096)], [ack(ob.T_DATA, 8192)], [ack(ob.T_DATA, 9000)], [nak(ob.T_END, 7)]])
        with self.assertRaises(ob.NakError) as cm:
            ob.Client(ser).install_app("oh", "OH", exts)
        self.assertIn("DUPLICATE", str(cm.exception))
        self.assertEqual(cm.exception.code, 7)
        self.assertEqual(ser.written[-1], FR["abort"])

    def test_no_space_nak_is_named(self):
        exts = [ob.Extent("orb_app", 0, 0x1F, 1, 0, img("guest_a"))]
        ser = FakeSerial([[nak(ob.T_BEGIN_APP, 12)]])
        with self.assertRaisesRegex(ob.NakError, "NO_SPACE"):
            ob.Client(ser).install_app("oh", "OH", exts)

    def test_hello_remove_pin(self):
        ser = FakeSerial([[FR["info_example"]], [ack(ob.T_REMOVE, 1)], [ack(ob.T_PIN, 1)], [ack(ob.T_PIN, 1)]])
        cl = ob.Client(ser)
        r = cl.hello()
        self.assertEqual(len(r["slots"]), 3)
        cl.remove(1)
        cl.pin(1, True)
        cl.pin(1, False)
        self.assertEqual(ser.written, [FR["hello"], FR["remove_1"], FR["pin_1_on"], FR["pin_1_off"]])

    def test_ignores_other_acks(self):
        ser = FakeSerial([[ack(ob.T_DATA, 5), nak(ob.T_BEGIN, 3), ack(ob.T_REMOVE, 1)]])
        ob.Client(ser).remove(1)

    def test_nak_names(self):
        for code, name in ob.NAK_NAMES.items():
            self.assertIn(name, str(ob.NakError(ob.T_BEGIN, code)))

    def test_install_taos_uses_the_classic_frames(self):
        t = img("taos")
        ser = FakeSerial([[ack(ob.T_BEGIN)], [ack(ob.T_DATA, 4096)], [ack(ob.T_DATA, 6000)], [ack(ob.T_END, 6000)]])
        ob.Client(ser).install_taos(t, "taOS")
        self.assertEqual(ser.written, [FR["begin_taos"], FR["data_taos_0"], FR["data_taos_1"], FR["end"]])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cat = write_catalog(self.tmp.name)
        self.err = io.StringIO()
        self.out = io.StringIO()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv, ser):
        with contextlib.redirect_stderr(self.err), contextlib.redirect_stdout(self.out):
            return ob.main(["-p", "x"] + argv, opener=lambda p: ser)

    def test_install_latest_by_default(self):
        ser = FakeSerial([[info_frame([TAOS])], [ack(ob.T_BEGIN_APP, 0)], [ack(ob.T_DATA, 4096)], [ack(ob.T_DATA, 5200)], [ack(ob.T_END, 5200)]])
        # 1.1 carries assets from the previous version only on update; on a fresh install its assets extent has no file: blank
        rc = self.run_cli(["install", "xiaozhi", "--catalog", self.cat], ser)
        self.assertEqual(rc, 0, self.err.getvalue())
        self.assertEqual(ser.written[0], FR["hello"])
        self.assertEqual(ser.written[1][4], ob.T_BEGIN_APP)
        payload = ser.written[1][7:-4]
        self.assertEqual(payload[:7], b"xiaozhi")
        self.assertEqual(payload[56], 2)
        self.assertEqual(struct.unpack_from("<I", payload, 57 + 20)[0], len(img("guest_b2")))          # latest = 1.1 = guest_b2
        self.assertEqual(struct.unpack_from("<I", payload, 57 + 56 + 20)[0], 0)                          # blank assets
        self.assertIn("installing xiaozhi 1.1", self.out.getvalue())

    def test_install_older_version_by_name(self):
        ser = FakeSerial([[info_frame([TAOS])], [ack(ob.T_BEGIN_APP, 0)]] + [[ack(ob.T_DATA, 0)]] * 4 + [[ack(ob.T_END, 0)]])
        rc = self.run_cli(["install", "xiaozhi", "--catalog", self.cat, "--version", "1.0"], ser)
        self.assertEqual(rc, 0, self.err.getvalue())
        want = [FR["hello"], FR["begin_app_xz"], FR["data_xz_e0_0"], FR["data_xz_e0_1"], FR["data_xz_e1_0"], FR["data_xz_e1_1"], FR["end"]]
        self.assertEqual(ser.written, want)      # exactly the shared vector frames: fresh install of 1.0 sends the assets too
        self.assertIn("1.0", self.out.getvalue())

    def test_downgrade_without_version_never_sends_begin(self):
        dev = info_frame([TAOS, entry_for(0, img("guest_b2"), "xiaozhi")])
        ser = FakeSerial([[dev]])
        rc = self.run_cli(["install", "xiaozhi", "--catalog", self.cat], ser)
        self.assertEqual(rc, 1)
        self.assertEqual(ser.written, [FR["hello"]])
        self.assertTrue(ser.closed)

    def test_downgrade_with_version_prints_it(self):
        dev = info_frame([TAOS, entry_for(0, img("guest_b2"), "xiaozhi")])
        ser = FakeSerial([[dev], [ack(ob.T_BEGIN_APP, 1)], [ack(ob.T_DATA, 0)], [ack(ob.T_DATA, 0)], [ack(ob.T_END, 0)]])
        rc = self.run_cli(["install", "xiaozhi", "--catalog", self.cat, "--version", "1.0"], ser)
        self.assertEqual(rc, 0, self.err.getvalue())
        self.assertIn("downgrade 1.1 -> 1.0", self.out.getvalue())

    def test_sha_mismatch_refused_before_begin(self):
        # a catalog whose recorded sha256 does not match the file
        with open(self.cat) as f:
            c = json.load(f)
        c["apps"][1]["versions"][0]["sha256"] = "ab" * 32
        with open(self.cat, "w") as f:
            json.dump(c, f)
        ser = FakeSerial([[info_frame([TAOS])]])
        rc = self.run_cli(["install", "openharness-stock", "--catalog", self.cat], ser)
        self.assertEqual(rc, 1)
        self.assertIn("sha256", self.err.getvalue())
        self.assertEqual(ser.written, [FR["hello"]])

    def test_update_all_skips_pinned_and_prints_them(self):
        b = img("guest_b")
        dev = info_frame([TAOS, entry_for(0, b, "xiaozhi", pinned=True), entry_for(1, img("guest_a"), "xz2")])
        ser = FakeSerial([[dev], [ack(ob.T_BEGIN_APP, 2)], [ack(ob.T_DATA, 0)], [ack(ob.T_DATA, 0)], [ack(ob.T_DATA, 0)], [ack(ob.T_END, 0)]])
        rc = self.run_cli(["update", "--all", "--catalog", self.cat], ser)
        self.assertEqual(rc, 0, self.err.getvalue())
        self.assertIn("skipping pinned", self.out.getvalue())
        begins = [w for w in ser.written if w[4] == ob.T_BEGIN_APP]
        self.assertEqual(len(begins), 1)
        self.assertEqual(begins[0][7:14], b"xz2\0\0\0\0")

    def test_update_single_pinned_entry_is_skipped(self):
        b = img("guest_b")
        ser = FakeSerial([[info_frame([TAOS, entry_for(0, b, "xiaozhi", pinned=True)])]])
        rc = self.run_cli(["update", "0", "--catalog", self.cat], ser)
        self.assertEqual(rc, 0)
        self.assertIn("skipping pinned", self.out.getvalue())
        self.assertEqual(ser.written, [FR["hello"]])

    def test_list_with_catalog(self):
        ser = FakeSerial([[info_frame([TAOS, entry_for(0, img("guest_b"), "xiaozhi", blocks=2)], free=21)]])
        rc = self.run_cli(["list", "--catalog", self.cat], ser)
        self.assertEqual(rc, 0, self.err.getvalue())
        self.assertIn("21 of 23 blocks free", self.out.getvalue())
        self.assertIn("update available -> 1.1", self.out.getvalue())

    def test_list_without_catalog(self):
        ser = FakeSerial([[FR["info_example"]]])
        rc = self.run_cli(["list"], ser)
        self.assertEqual(rc, 0, self.err.getvalue())
        self.assertIn("19 of 23 blocks free", self.out.getvalue())
        self.assertIn("openharness-taos", self.out.getvalue())

    def test_versions_command_needs_no_device(self):
        with contextlib.redirect_stdout(self.out):
            rc = ob.main(["versions", "zclaw", "--catalog", self.cat], opener=lambda p: self.fail("must not open the port"))
        self.assertEqual(rc, 0)
        self.assertIn("yanked", self.out.getvalue())

    def test_remove_pin_unpin(self):
        ser = FakeSerial([[ack(ob.T_REMOVE, 1)]])
        self.assertEqual(self.run_cli(["remove", "1"], ser), 0)
        self.assertEqual(ser.written, [FR["remove_1"]])
        ser = FakeSerial([[ack(ob.T_PIN, 1)]])
        self.assertEqual(self.run_cli(["pin", "1"], ser), 0)
        self.assertEqual(ser.written, [FR["pin_1_on"]])
        ser = FakeSerial([[ack(ob.T_PIN, 1)]])
        self.assertEqual(self.run_cli(["unpin", "1"], ser), 0)
        self.assertEqual(ser.written, [FR["pin_1_off"]])

    def test_slot0_cannot_be_removed_or_pinned(self):
        for argv in (["remove", "255"], ["pin", "255"]):
            ser = FakeSerial([])
            self.assertEqual(self.run_cli(argv, ser), 1)
            self.assertEqual(ser.written, [])

    def test_nak_exit_code_and_name(self):
        ser = FakeSerial([[nak(ob.T_REMOVE, 8)]])
        self.assertEqual(self.run_cli(["remove", "1"], ser), 1)
        self.assertIn("BUSY", self.err.getvalue())

    def test_full_chip_image_refused_before_the_port_opens(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ob.main(["-p", "x", "taos", os.path.join(VEC, "orb_store_img_fullchip.bin")],
                         opener=lambda p: self.fail("must not open the port"))
        self.assertEqual(rc, 1)
        self.assertIn("full-chip", err.getvalue())


if __name__ == "__main__":
    unittest.main()
