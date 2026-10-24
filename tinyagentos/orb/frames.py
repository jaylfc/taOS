"""Orb wire protocol: frame codec and shared constants."""
import struct
import zlib

MAGIC = b"ORBS"
MAX_PAYLOAD = 4096 + 8
CHUNK = 4096
T_HELLO, T_BEGIN, T_DATA, T_END, T_REMOVE, T_ABORT, T_BEGIN_APP, T_PIN = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08
T_NAK, T_ACK, T_INFO, T_PROGRESS = 0x7F, 0x80, 0x81, 0x82

NAK_NAMES = {
    1: "BAD_CRC", 2: "BAD_SLOT", 3: "TOO_BIG", 4: "BAD_OFFSET", 5: "BAD_IMAGE", 6: "HASH_MISMATCH",
    7: "DUPLICATE", 8: "BUSY", 9: "NOT_ALLOWED", 10: "FLASH_ERR", 11: "NO_SESSION", 12: "NO_SPACE",
}
TYPE_NAMES = {1: "HELLO", 2: "BEGIN", 3: "DATA", 4: "END", 5: "REMOVE", 6: "ABORT", 7: "BEGIN_APP", 8: "PIN"}


class OrbError(Exception):
    pass


class TransportUnavailable(OrbError):
    """pyserial (the `orb` extra) is not installed"""


class NakError(OrbError):
    def __init__(self, for_type, code):
        self.for_type, self.code = for_type, code
        OrbError.__init__(self, "device refused %s: NAK %s (%d)" % (
            TYPE_NAMES.get(for_type, "0x%02x" % for_type), NAK_NAMES.get(code, "UNKNOWN"), code))


# ------------------------------------------------------------------ framing ----
def encode_frame(ftype, payload=b""):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long")
    body = struct.pack("<BH", ftype, len(payload)) + bytes(payload)
    return MAGIC + body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


class FrameDecoder:
    """Resyncing decoder: scans for "ORBS", ignores everything else (console logs), drops bad-CRC frames.
    After a bad frame it rescans the bytes it had consumed, so a good frame hidden inside is still found."""

    def __init__(self):
        self.buf = bytearray()
        self.bad_crc = 0

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(MAGIC)
            if i < 0:
                keep = 0                      # keep a partial magic at the tail
                for k in (3, 2, 1):
                    if self.buf.endswith(MAGIC[:k]):
                        keep = k
                        break
                del self.buf[:len(self.buf) - keep]
                return out
            del self.buf[:i]
            if len(self.buf) < 7:
                return out
            ftype, ln = struct.unpack_from("<BH", self.buf, 4)
            if ln > MAX_PAYLOAD:
                self.bad_crc += 1
                del self.buf[:4]
                continue
            total = 4 + 3 + ln + 4
            if len(self.buf) < total:
                return out
            body = bytes(self.buf[4:7 + ln])
            (crc,) = struct.unpack_from("<I", self.buf, 7 + ln)
            if crc != zlib.crc32(body) & 0xFFFFFFFF:
                self.bad_crc += 1
                del self.buf[:4]
                continue
            out.append((ftype, body[3:]))
            del self.buf[:total]


def cstr(s, n):
    b = s.encode("utf-8")
    if len(b) >= n:
        raise ValueError("%r is longer than %d bytes" % (s, n - 1))
    return b + b"\0" * (n - len(b))


def from_cstr(b):
    return bytes(b).split(b"\0", 1)[0].decode("utf-8", "replace")

