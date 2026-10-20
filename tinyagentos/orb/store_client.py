"""Store client: request/response over a serial port."""
import hashlib
import struct
import time

from .frames import (CHUNK, FrameDecoder, NakError, OrbError, T_ABORT, T_ACK, T_BEGIN, T_BEGIN_APP, T_DATA, T_END,
                     T_HELLO, T_INFO, T_NAK, T_PIN, T_REMOVE, encode_frame)
from .protocol import begin_app_payload, begin_payload, parse_info


class Client:
    def __init__(self, ser):
        self.ser = ser
        self.dec = FrameDecoder()
        self.pending = []

    def _send(self, ftype, payload=b""):
        self.ser.write(encode_frame(ftype, payload))
        flush = getattr(self.ser, "flush", None)
        if flush:
            flush()

    def _next(self, deadline):
        while not self.pending:
            if time.time() > deadline:
                raise OrbError("timeout waiting for the device (is the launcher on screen?)")
            data = self.ser.read(4096)
            if data:
                if b"waiting for download" in data:
                    raise OrbError("the chip is in ROM download mode; power-cycle the Orb")
                self.pending.extend(self.dec.feed(data))
        return self.pending.pop(0)

    def request(self, ftype, payload=b"", timeout=5.0, want=T_ACK):
        """Send one frame, return the payload of the matching ACK/INFO; raise NakError on a NAK."""
        self._send(ftype, payload)
        deadline = time.time() + timeout
        while True:
            rtype, rp = self._next(deadline)
            if rtype == T_NAK and len(rp) == 2 and rp[0] == ftype:
                raise NakError(rp[0], rp[1])
            if rtype == want and (want != T_ACK or (len(rp) == 5 and rp[0] == ftype)):
                return rp

    def hello(self):
        return parse_info(self.request(T_HELLO, want=T_INFO))

    def _session(self, begin_type, begin_payload_, sends, progress, total, begin_timeout=90.0):
        """BEGIN (the device erases and may compact: long timeout), DATA frames, END; ABORT on any failure."""
        rp = self.request(begin_type, begin_payload_, timeout=begin_timeout)
        entry = struct.unpack_from("<I", rp, 1)[0]
        try:
            done = 0
            for frame_payload, n in sends:
                self.request(T_DATA, frame_payload, timeout=10.0)
                done += n
                if progress:
                    progress(done, total)
            self.request(T_END, timeout=30.0)
        except BaseException:
            try:
                self._send(T_ABORT)
            except Exception:
                pass
            raise
        return entry

    def install_app(self, app_id, name, exts, add=False, progress=None):
        """Install (or update) an app: BEGIN_APP with the extent list, DATA per extent in order, END. Returns the entry index."""
        sends = []
        for j, x in enumerate(exts):
            if x.blob:
                for off in range(0, len(x.blob), CHUNK):
                    chunk = x.blob[off:off + CHUNK]
                    sends.append((struct.pack("<BI", j, off) + chunk, len(chunk)))
        total = sum(len(x.blob) for x in exts if x.blob)
        return self._session(T_BEGIN_APP, begin_app_payload(app_id, name, exts, add), sends, progress, total)

    def install_taos(self, image, name, progress=None):
        sha = hashlib.sha256(image).digest()
        sends = [(struct.pack("<I", off) + image[off:off + CHUNK], len(image[off:off + CHUNK])) for off in range(0, len(image), CHUNK)]
        self._session(T_BEGIN, begin_payload(0, image, name, sha), sends, progress, len(image))

    def remove(self, index):
        self.request(T_REMOVE, bytes([index]), timeout=10.0)

    def pin(self, index, on):
        self.request(T_PIN, bytes([index, 1 if on else 0]), timeout=10.0)


# ------------------------------------------------------------------ cli ----
