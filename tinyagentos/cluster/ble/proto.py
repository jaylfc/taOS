"""taosble.proto - pure protocol core for the taOSusb Bluetooth pairing handshake (S1).

No D-Bus, no sockets, no files: every function/class here takes bytes/values in and returns
bytes/values out. That's on purpose - this module is vendored byte-identical into the taOS
controller (the other end of the handshake), so it stays importable on x86 for tests and reusable
by both sides. Only the standard library and `cryptography` are allowed as dependencies.

Wire protocol (see docs/taosusb-pairing-plan.md "Design" and the S1 spec for the exact grammar):
  * `info` (read): UTF-8 JSON describing the board - see info_frame().
  * `pair` (write+notify): fragmented messages (see fragment()/Reassembler), each a UTF-8 JSON
    object. Handshake: plaintext "hello" x2, then "sealed" (ChaCha20-Poly1305, AAD b"pair") for
    everything after the session key exists. PairResponder is the board side, PairInitiator the
    controller side.
  * `link` (write+notify): defined but not implemented in S1 - see LinkResponder.
"""
import hashlib
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from cryptography.hazmat.primitives import hashes

# ---- identity -----------------------------------------------------------------------------

SERVICE_UUID = "ae4bdca2-1e41-4d0a-b0e4-c7d093aa60b5"
CHAR_INFO_UUID = "8f76242e-47af-4c85-94c7-97b1e9a63efb"
CHAR_PAIR_UUID = "58efa953-abac-405e-a545-d88afa03c23a"
CHAR_LINK_UUID = "a62eccdf-7ac9-4ccc-a214-71ef5baaca0b"

ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O/1/I - unambiguous when read off a screen

# ---- framing --------------------------------------------------------------------------------

FLAG_FIRST = 0x01
FLAG_LAST = 0x02
MAX_MESSAGE = 8192      # bytes, reassembled
MAX_INFLIGHT = 4         # distinct message ids being reassembled at once

# ---- pairing window / retry budget -----------------------------------------------------------

# Jay (2026-09-23): an unpaired board advertises and stays pairable UNTIL PAIRED, so it is ready
# out of the box. None = no time limit; a number still bounds it (the future pairing button may
# want one). A run of failed provisions locks pairing for a while, doubling each time up to a
# cap, rather than closing it for good: with no time limit, "closed for good" would leave a
# headless board unpairable until someone power-cycled it.
PAIR_WINDOW_S = None
MAX_FAILED_PROVISIONS = 5
LOCKOUT_S = 60.0
LOCKOUT_MAX_S = 900.0

# ---- advertisement identifier ------------------------------------------------------------------
# Manufacturer data, so a taOS controller can tell a taOS board from every other BLE device (and
# see its state) from the advert alone, before connecting. 0xFFFF is the Bluetooth SIG's id for
# unassigned/test use; taOS has no company id of its own.
MFR_ID = 0xFFFF
MFR_MAGIC = b"taOS"
PROTO_VERSION = 1
STATE_PAIRED = 0x01


def advert_mfr_data(paired):
    return MFR_MAGIC + bytes([PROTO_VERSION, STATE_PAIRED if paired else 0])


def parse_advert_mfr(data):
    """-> {"v", "paired"} for a taOS advert, None for anything else (never raises)."""
    try:
        data = bytes(data)
    except (TypeError, ValueError):
        return None
    if len(data) != len(MFR_MAGIC) + 2 or not data.startswith(MFR_MAGIC):
        return None
    return {"v": data[4], "paired": bool(data[5] & STATE_PAIRED)}

# ---- provisioning field validation -----------------------------------------------------------

NODE_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
LLM_KEY_RE = re.compile(r"^[\x21-\x7e]{16,256}$")   # printable ASCII, no whitespace
SSID_MAX_BYTES = 32
CONTROLLER_ID_MAX = 128
URLS_MAX = 8
WIFI_MAX = 16
MESH_PREAUTH_MAX = 4096


def b64(data):
    import base64
    return base64.b64encode(data).decode("ascii")


def unb64(s, expect_len=None):
    """Strict base64 decode: rejects non-str input and (if given) any but the expected length."""
    import base64
    if not isinstance(s, str):
        raise ValueError("not a string")
    try:
        data = base64.b64decode(s, validate=True)
    except (ValueError, TypeError):
        raise ValueError("bad base64")
    if expect_len is not None and len(data) != expect_len:
        raise ValueError("bad decoded length")
    return data


# ---- X25519 key helpers -----------------------------------------------------------------------

def x25519_keypair():
    priv = X25519PrivateKey.generate()
    return priv, priv.public_key()


def pub_bytes(pub):
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_from_raw(raw):
    """Load a static private key from its 32 raw bytes (what's persisted in ble.key)."""
    return X25519PrivateKey.from_private_bytes(raw)


def raw_from_key(priv):
    return priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def random_board_id(n=4):
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(n))


# ---- fragmentation / reassembly ----------------------------------------------------------------

def fragment(payload, mid, chunk_size):
    """Split `payload` into wire fragments for message id `mid` (0-255). `chunk_size` is the
    caller's MTU budget for the payload part (MTU - 3 ATT overhead - 2 our header)."""
    if not (0 <= mid <= 255):
        raise ValueError("mid must be 0-255")
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    chunks = [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)] or [b""]
    out = []
    for i, c in enumerate(chunks):
        flags = (FLAG_FIRST if i == 0 else 0) | (FLAG_LAST if i == len(chunks) - 1 else 0)
        out.append(bytes([flags, mid]) + c)
    return out


class Reassembler:
    """Reassembles fragmented `pair`/`link` writes (or notifies) by message id. This is NOT a
    reliable transport: an orphan continuation fragment, a second FIRST for a still-open id, or a
    message that grows past max_message all just drop that message (feed() returns None forever
    for it) - the sender has to pick a fresh id and resend whole. One Reassembler per direction
    per characteristic per connection."""

    def __init__(self, max_message=MAX_MESSAGE, max_inflight=MAX_INFLIGHT):
        self.max_message = max_message
        self.max_inflight = max_inflight
        self._buf = {}   # mid -> bytearray, insertion order == arrival order of each id's FIRST

    def feed(self, frag):
        """Feed one wire fragment. Returns the reassembled payload (bytes) when a message
        completes, else None."""
        if len(frag) < 2:
            return None   # too short to carry flags+mid; not a real fragment
        flags, mid = frag[0], frag[1]
        chunk = frag[2:]
        first, last = bool(flags & FLAG_FIRST), bool(flags & FLAG_LAST)
        if first:
            if mid in self._buf:
                # A second FIRST for an id still being reassembled is corruption, not a resend:
                # drop the in-flight message. The sender must use a fresh id to try again.
                del self._buf[mid]
                return None
            if len(self._buf) >= self.max_inflight:
                return None   # too many messages in flight; drop the new one
            self._buf[mid] = bytearray(chunk)
        else:
            if mid not in self._buf:
                return None   # orphan continuation fragment: nothing to append to
            self._buf[mid].extend(chunk)
        buf = self._buf.get(mid)
        if buf is not None and len(buf) > self.max_message:
            del self._buf[mid]
            return None
        if last:
            return bytes(self._buf.pop(mid, b""))
        return None


# ---- transcript / code / session key -----------------------------------------------------------

def transcript(board_id, cpub, c_epub, c_n, bpub, b_epub, b_n):
    for name, val, n in (("cpub", cpub, 32), ("c_epub", c_epub, 32), ("c_n", c_n, 16),
                         ("bpub", bpub, 32), ("b_epub", b_epub, 32), ("b_n", b_n, 16)):
        if len(val) != n:
            raise ValueError("bad %s length" % name)
    return hashlib.sha256(b"taos-ble-v1" + board_id.encode("ascii") + cpub + c_epub + c_n +
                          bpub + b_epub + b_n).digest()


def derive_code(T):
    h = hashlib.sha256(b"code" + T).digest()[:4]
    return "%06d" % (int.from_bytes(h, "big") % 1_000_000)


def _reject_weak(secret):
    # X25519 accepts any 32 bytes as a public key; a low-order/identity point yields an
    # all-zero (or otherwise degenerate) shared secret. Cheap to check, worth checking.
    if secret == b"\x00" * len(secret):
        raise ValueError("weak/low-order key material")
    return secret


def derive_session_key(eph_priv, eph_peer_pub, static_priv, static_peer_pub, T):
    ikm = (_reject_weak(eph_priv.exchange(eph_peer_pub)) +
           _reject_weak(static_priv.exchange(static_peer_pub)))
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=T, info=b"taos-ble-pair-v1").derive(ikm)


# ---- sealed messages --------------------------------------------------------------------------

def seal(key, aad, plaintext, nonce):
    return nonce + ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)


def unseal(key, aad, blob):
    if len(blob) < 12 + 16:
        raise ValueError("sealed blob too short")
    nonce, ct = blob[:12], blob[12:]
    return ChaCha20Poly1305(key).decrypt(nonce, ct, aad)   # raises InvalidTag on tamper


def _err(why):
    return json.dumps({"t": "error", "why": why}).encode("utf-8")


def _seal_frame(session_key, aad, obj):
    nonce = os.urandom(12)
    blob = seal(session_key, aad, json.dumps(obj).encode("utf-8"), nonce)
    return json.dumps({"t": "sealed", "c": b64(blob)}).encode("utf-8")


# ---- provisioning payload validation ------------------------------------------------------------

def _valid_url(u):
    """http(s) only, a real host, no userinfo/query/fragment, no whitespace/control chars."""
    if not isinstance(u, str) or not (1 <= len(u) <= 2048):
        return False
    if any(ord(c) < 0x21 for c in u):
        return False
    try:
        p = urlsplit(u)
        p.port   # raises ValueError if the port isn't a valid int
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    if not p.hostname:
        return False
    if p.username is not None or p.password is not None:
        return False
    if p.query or p.fragment:
        return False
    return True


def _valid_llm_base(u):
    return _valid_url(u) and urlsplit(u).path.endswith("/api/llm/v1")


def validate_provision(msg):
    """Validate a decrypted `provision` payload strictly. Returns (True, None, fields) with a
    clean dict on success, or (False, why, None) - `why` is short and safe to send back sealed."""
    if not isinstance(msg, dict):
        return False, "not an object", None
    allowed = {"t", "controller_id", "controller_urls", "node_key", "wifi", "mesh_preauth", "llm"}
    if set(msg.keys()) - allowed:
        return False, "unexpected field", None

    cid = msg.get("controller_id")
    if not isinstance(cid, str) or not (1 <= len(cid) <= CONTROLLER_ID_MAX):
        return False, "bad controller_id", None

    urls = msg.get("controller_urls")
    if (not isinstance(urls, list) or not (1 <= len(urls) <= URLS_MAX) or
            not all(_valid_url(u) for u in urls)):
        return False, "bad controller_urls", None

    node_key = msg.get("node_key")
    if not isinstance(node_key, str) or not NODE_KEY_RE.match(node_key):
        return False, "bad node_key", None

    wifi = msg.get("wifi")
    if not isinstance(wifi, list) or len(wifi) > WIFI_MAX:
        return False, "bad wifi", None
    clean_wifi = []
    for w in wifi:
        if not isinstance(w, dict) or set(w.keys()) != {"ssid", "psk"}:
            return False, "bad wifi entry", None
        ssid, psk = w.get("ssid"), w.get("psk")
        if not isinstance(ssid, str) or not (1 <= len(ssid.encode("utf-8")) <= SSID_MAX_BYTES):
            return False, "bad ssid", None
        if not isinstance(psk, str) or not (psk == "" or 8 <= len(psk) <= 63):
            return False, "bad psk", None
        clean_wifi.append({"ssid": ssid, "psk": psk})

    preauth = msg.get("mesh_preauth")
    if preauth is not None and (not isinstance(preauth, str) or not (1 <= len(preauth) <= MESH_PREAUTH_MAX)):
        return False, "bad mesh_preauth", None

    llm = msg.get("llm")
    if llm is not None:
        if not isinstance(llm, dict) or set(llm.keys()) != {"base", "key"}:
            return False, "bad llm", None
        base, key = llm.get("base"), llm.get("key")
        if not _valid_llm_base(base):
            return False, "bad llm base", None
        if not isinstance(key, str) or not LLM_KEY_RE.match(key):
            return False, "bad llm key", None
        llm = {"base": base, "key": key}

    return True, None, {
        "controller_id": cid, "controller_urls": list(urls), "node_key": node_key.lower(),
        "wifi": clean_wifi, "mesh_preauth": preauth, "llm": llm,
    }


# ---- info characteristic -----------------------------------------------------------------------

def info_frame(board_id, name, state, pairable, static_pub_raw):
    return json.dumps({"v": 1, "id": board_id, "name": name, "caps": ["agent"], "state": state,
                       "pairable": bool(pairable), "bpub": b64(static_pub_raw)}).encode("utf-8")


# ---- board side: PairResponder -------------------------------------------------------------------

class PairResponder:
    """Board side of the `pair` handshake. One instance per daemon lifetime. Feed it fully
    reassembled `pair` messages via handle_message(); it always returns bytes to notify back -
    even failures are a reply (an error frame), never an exception, never silence."""

    def __init__(self, board_id, static_priv, *, paired=False, window_s=PAIR_WINDOW_S,
                 now=time.time, max_failed_provisions=MAX_FAILED_PROVISIONS):
        self.board_id = board_id
        self.static_priv = static_priv
        self.static_pub = static_priv.public_key()
        self.paired = paired
        self.window_s = window_s
        self.now = now
        self.start = now()
        self.max_failed = max_failed_provisions
        self.failed = 0
        self.locked_until = 0.0
        self.lockout_s = LOCKOUT_S
        self._session = None
        self.provisioned = None   # set on a successful provision (see validate_provision's fields)

    def window_open(self):
        now = self.now()
        if now < self.locked_until:
            return False
        return self.window_s is None or (now - self.start) < self.window_s

    def current_code(self):
        return self._session["code"] if self._session else None

    def handle_message(self, raw):
        try:
            return self._handle_message(raw)
        except Exception as e:  # noqa: BLE001 - a protocol bug must not crash the GATT daemon
            return _err("internal error: %s" % type(e).__name__)

    def _handle_message(self, raw):
        if self.paired:
            return _err("already paired")
        if not self.window_open():
            return _err("pairing window closed")
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _err("bad json")
        if not isinstance(msg, dict):
            return _err("bad frame")
        t = msg.get("t")
        if t == "hello":
            return self._on_hello(msg)
        if t == "sealed":
            return self._on_sealed(msg)
        return _err("unknown type")

    def _on_hello(self, msg):
        try:
            cpub = unb64(msg["cpub"], 32)
            c_epub = unb64(msg["epub"], 32)
            c_n = unb64(msg["n"], 16)
        except (KeyError, ValueError, TypeError):
            return _err("bad hello")
        b_epriv, b_epub = x25519_keypair()
        b_n = os.urandom(16)
        bpub_bytes, b_epub_bytes = pub_bytes(self.static_pub), pub_bytes(b_epub)
        try:
            T = transcript(self.board_id, cpub, c_epub, c_n, bpub_bytes, b_epub_bytes, b_n)
            key = derive_session_key(b_epriv, X25519PublicKey.from_public_bytes(c_epub),
                                      self.static_priv, X25519PublicKey.from_public_bytes(cpub), T)
        except ValueError:
            return _err("bad hello")
        # A second hello mid-session resets the session (new keys, new code); the failed-provision
        # budget is per WINDOW, not per session, so it is deliberately NOT reset here.
        self._session = {"T": T, "code": derive_code(T), "key": key, "cpub": cpub, "seen_nonces": set()}
        return json.dumps({"t": "hello", "bpub": b64(bpub_bytes), "epub": b64(b_epub_bytes),
                           "n": b64(b_n)}).encode("utf-8")

    def _on_sealed(self, msg):
        if self._session is None:
            return _err("no session")
        sess = self._session
        try:
            blob = unb64(msg["c"])
        except (KeyError, ValueError, TypeError):
            return _err("bad sealed")
        if len(blob) < 12:
            return _err("bad sealed")
        nonce = blob[:12]
        if nonce in sess["seen_nonces"]:
            return _seal_frame(sess["key"], b"pair", {"t": "error", "why": "nonce reuse"})
        sess["seen_nonces"].add(nonce)
        try:
            pt = unseal(sess["key"], b"pair", blob)
            inner = json.loads(pt.decode("utf-8"))
        except (InvalidTag, ValueError, UnicodeDecodeError):
            return _seal_frame(sess["key"], b"pair", {"t": "error", "why": "bad ciphertext"})
        if not isinstance(inner, dict) or inner.get("t") != "provision":
            return _seal_frame(sess["key"], b"pair", {"t": "error", "why": "unknown sealed type"})
        return self._on_provision(inner, sess)

    def _on_provision(self, msg, sess):
        ok, why, fields = validate_provision(msg)
        if not ok:
            self.failed += 1
            if self.failed >= self.max_failed:
                self.locked_until = self.now() + self.lockout_s
                self.lockout_s = min(self.lockout_s * 2, LOCKOUT_MAX_S)
                self.failed = 0
                self._session = None
            return _seal_frame(sess["key"], b"pair", {"t": "error", "why": why})
        fields["cpub"] = b64(sess["cpub"])
        self.provisioned = fields
        self.paired = True
        return _seal_frame(sess["key"], b"pair", {"t": "ok"})


# ---- controller side: PairInitiator ---------------------------------------------------------------

class PairInitiator:
    """Controller side of the `pair` handshake. Not the hardened half (the controller is already
    admin-gated) but shares the same primitives so both ends can never disagree about the math."""

    def __init__(self, controller_id, static_priv):
        self.controller_id = controller_id
        self.static_priv = static_priv
        self.static_pub = static_priv.public_key()
        self._pending = None
        self._session = None

    def current_code(self):
        return self._session["code"] if self._session else None

    def start_hello(self):
        c_epriv, c_epub = x25519_keypair()
        c_n = os.urandom(16)
        self._pending = {"epriv": c_epriv, "epub": c_epub, "n": c_n}
        return json.dumps({"t": "hello", "cpub": b64(pub_bytes(self.static_pub)),
                           "epub": b64(pub_bytes(c_epub)), "n": b64(c_n)}).encode("utf-8")

    def on_hello_reply(self, board_id, raw):
        """Parse the board's hello reply, derive the session, return the 6-digit code to show."""
        msg = json.loads(raw.decode("utf-8"))
        if msg.get("t") != "hello":
            raise ValueError("expected hello, got %r" % msg.get("t"))
        bpub = unb64(msg["bpub"], 32)
        b_epub = unb64(msg["epub"], 32)
        b_n = unb64(msg["n"], 16)
        p = self._pending
        T = transcript(board_id, pub_bytes(self.static_pub), pub_bytes(p["epub"]), p["n"],
                       bpub, b_epub, b_n)
        key = derive_session_key(p["epriv"], X25519PublicKey.from_public_bytes(b_epub),
                                  self.static_priv, X25519PublicKey.from_public_bytes(bpub), T)
        self._session = {"T": T, "code": derive_code(T), "key": key, "bpub": bpub, "seen_nonces": set()}
        return self._session["code"]

    def seal_provision(self, provision):
        pt = json.dumps(dict(provision, t="provision")).encode("utf-8")
        nonce = os.urandom(12)
        blob = seal(self._session["key"], b"pair", pt, nonce)
        return json.dumps({"t": "sealed", "c": b64(blob)}).encode("utf-8")

    def unseal_reply(self, raw):
        msg = json.loads(raw.decode("utf-8"))
        if msg.get("t") == "error":
            return msg
        if msg.get("t") != "sealed":
            raise ValueError("expected sealed, got %r" % msg.get("t"))
        blob = unb64(msg["c"])
        sess = self._session
        nonce = blob[:12]
        if nonce in sess["seen_nonces"]:
            raise ValueError("nonce reuse in reply")
        sess["seen_nonces"].add(nonce)
        pt = unseal(sess["key"], b"pair", blob)
        return json.loads(pt.decode("utf-8"))


# ---- link characteristic: S1 stub ------------------------------------------------------------------

class LinkResponder:
    """S1 stub for `link`: the characteristic exists (so it advertises and a controller can probe
    it) but the full link protocol - negotiation, heartbeat, chat - is S2. Every message gets a
    plain error frame; there is no state and no gating here beyond that."""

    def handle_message(self, raw):
        return _err("link not implemented yet (S2)")
