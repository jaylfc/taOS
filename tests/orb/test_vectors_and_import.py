"""Drift guard for the Orb client core: the independent vector generator must reproduce the checked-in vectors, the
firmware's INFO shape on main must parse, and the package must import without pyserial."""
import os
import struct
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "..", "fixtures", "orb")
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def test_vectors_regenerate_byte_identical(tmp_path):
    subprocess.run([sys.executable, os.path.join(FIX, "gen_store_vectors.py"), str(tmp_path)], check=True,
                   capture_output=True)
    want = sorted(f for f in os.listdir(FIX) if f.endswith(".bin"))
    got = sorted(f for f in os.listdir(tmp_path) if f.endswith(".bin"))
    assert got == want
    for name in want:
        with open(os.path.join(FIX, name), "rb") as a, open(tmp_path / name, "rb") as b:
            assert a.read() == b.read(), name


def test_main_shaped_info_record_parses():
    from tinyagentos import orb
    from tinyagentos.orb.protocol import INFO_FIXED

    assert INFO_FIXED == 126

    def cs(s, n):
        return s.encode() + b"\0" * (n - len(s))

    # u8 index, u32 size, u8 present, project_name[32], version[32], elf_sha[32], display_name[24]; no app_id/pinned/blocks
    rec = (struct.pack("<BIB", 0xFF, 0x300000, 1) + cs("taos-orb", 32) + cs("1.0.0", 32) + bytes(range(32))
           + cs("taOS", 24))
    assert len(rec) == INFO_FIXED
    payload = struct.pack("<BHBB", 1, INFO_FIXED, 3, 12) + rec
    info = orb.parse_info(payload)
    assert info["free"] == 3 and info["total"] == 12
    (s,) = info["slots"]
    assert s["index"] == 0xFF and s["size"] == 0x300000 and s["present"] is True
    assert s["project_name"] == "taos-orb" and s["version"] == "1.0.0" and s["display_name"] == "taOS"
    assert s["elf_sha256"] == bytes(range(32))
    assert s["app_id"] == "" and s["pinned"] is False and s["blocks"] == 0


def test_import_without_pyserial():
    code = (
        "import sys\n"
        "sys.modules['serial'] = None\n"
        "import tinyagentos.orb as orb\n"
        "from tinyagentos.orb.transport import open_port\n"
        "try:\n"
        "    open_port('/dev/null')\n"
        "except ImportError:\n"
        "    sys.exit(0)\n"
        "sys.exit(3)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
