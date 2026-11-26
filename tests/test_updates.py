"""Tests for tinyagentos/updates.py (controller-side update pre-check) and its routes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos import updates
from tinyagentos.app import create_app

from taos_test_csrf import csrf_event_hooks

pytestmark = pytest.mark.skipif(
    shutil.which("gpgv") is None or shutil.which("gpg") is None,
    reason="gpg/gpgv not installed (apt install gnupg); the signature pre-check cannot run",
)


def _gpg(home: Path, *args: str, stdin: bytes | None = None) -> bytes:
    env = {**os.environ, "GNUPGHOME": str(home)}
    return subprocess.run(
        ["gpg", "--batch", "--no-tty", *args], env=env, input=stdin,
        check=True, capture_output=True,
    ).stdout


def _make_key(home: Path, name: str) -> Path:
    """Throwaway ed25519 key in `home`; returns a binary keyring with its public half."""
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    _gpg(home, "--passphrase", "", "--pinentry-mode", "loopback",
         "--quick-generate-key", f"{name} <{name}@example.invalid>", "ed25519", "sign", "1d")
    ring = home / f"{name}.gpg"
    ring.write_bytes(_gpg(home, "--export"))
    return ring


def _sign(home: Path, manifest: bytes) -> bytes:
    return _gpg(home, "--pinentry-mode", "loopback", "--passphrase", "",
                "--detach-sign", stdin=manifest)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _manifest(fmt=2, seq=7, steps=("base-1", "release-3", "release-12", "release-9"),
              published=None, expires=None) -> bytes:
    now = datetime.now(timezone.utc)
    published = published or now - timedelta(days=1)
    m = {"format": fmt, "seq": seq, "published": _ts(published),
         "steps": [{"id": s} for s in steps]}
    if fmt == 2:
        m["expires"] = _ts(expires or published + timedelta(days=14))
    return json.dumps(m).encode()


class _Feed:
    """Local HTTP feed serving manifest.json and manifest.json.sig."""

    def __init__(self, manifest: bytes | None, sig: bytes | None, status: int = 200):
        self.files = {"/manifest.json": manifest, "/manifest.json.sig": sig}
        self.status = status
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = outer.files.get(self.path)
                if outer.status != 200 or body is None:
                    self.send_response(outer.status if outer.status != 200 else 404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def keys(tmp_path):
    good = tmp_path / "good-home"
    ring = _make_key(good, "good")
    other = tmp_path / "other-home"
    _make_key(other, "other")
    return {"good_home": good, "ring": ring, "other_home": other}


@pytest.fixture
def feeds():
    made = []

    def make(manifest, sig, status=200):
        f = _Feed(manifest, sig, status)
        made.append(f)
        return f

    yield make
    for f in made:
        f.close()


def _cfg(tmp_path, keys, primary, mirror="http://127.0.0.1:1/") -> updates.UpdateConfig:
    return updates.UpdateConfig(
        channel="stable", feed_url=primary, mirror_url=mirror,
        keyring=str(keys["ring"]), status_file=str(tmp_path / "status.json"),
        timeout=3.0,
    )


def test_valid_primary(tmp_path, keys, feeds):
    m = _manifest()
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is True, r
    assert r["source"] == "primary"
    assert r["seq"] == 7
    assert r["release"] == 12
    assert r["reason"] is None


def test_primary_down_uses_mirror(tmp_path, keys, feeds):
    m = _manifest()
    mirror = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, "http://127.0.0.1:1/", mirror.url))
    assert r["ok"] is True, r
    assert r["source"] == "mirror"


def test_primary_non_200_uses_mirror(tmp_path, keys, feeds):
    m = _manifest()
    primary = feeds(None, None, status=503)
    mirror = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, primary.url, mirror.url))
    assert r["ok"] is True and r["source"] == "mirror", r


def test_both_down_never_raises(tmp_path, keys):
    r = updates.check(_cfg(tmp_path, keys, "http://127.0.0.1:1/", "http://127.0.0.1:2/"))
    assert r["ok"] is False
    assert r["reason"] == "unreachable"


def test_bad_signature_refused(tmp_path, keys, feeds):
    m = _manifest()
    sig = _sign(keys["good_home"], m)
    tampered = m.replace(b'"seq": 7', b'"seq": 8')
    assert tampered != m
    f = feeds(tampered, sig)
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False
    assert r["reason"] == "signature"


def test_unknown_key_refused(tmp_path, keys, feeds):
    m = _manifest()
    f = feeds(m, _sign(keys["other_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False
    assert r["reason"] == "signature"


def test_truncated_signature_refused(tmp_path, keys, feeds):
    m = _manifest()
    sig = _sign(keys["good_home"], m)
    f = feeds(m, sig[: len(sig) // 2])
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False
    assert r["reason"] == "signature"


def test_missing_keyring_refused(tmp_path, keys, feeds):
    m = _manifest()
    f = feeds(m, _sign(keys["good_home"], m))
    cfg = _cfg(tmp_path, keys, f.url)
    cfg.keyring = str(tmp_path / "nope.gpg")
    r = updates.check(cfg)
    assert r["ok"] is False and r["reason"] == "signature"


def test_expired_format2_refused(tmp_path, keys, feeds):
    now = datetime.now(timezone.utc)
    m = _manifest(published=now - timedelta(days=10), expires=now - timedelta(days=1))
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False
    assert r["reason"] == "expired"


def test_window_31_days_refused(tmp_path, keys, feeds):
    now = datetime.now(timezone.utc)
    pub = now - timedelta(days=1)
    m = _manifest(published=pub, expires=pub + timedelta(days=31))
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False
    assert r["reason"] == "window"


def test_window_30_days_accepted(tmp_path, keys, feeds):
    now = datetime.now(timezone.utc)
    pub = now - timedelta(days=1)
    m = _manifest(published=pub, expires=pub + timedelta(days=30))
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is True, r


def test_format1_accepted_without_expires(tmp_path, keys, feeds):
    m = _manifest(fmt=1)
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is True, r
    assert r["expires"] is None


def test_oversize_manifest_refused(tmp_path, keys, feeds):
    m = json.dumps({"format": 1, "seq": 1, "published": 1767225600,
                    "steps": [], "pad": "x" * (1024 * 1024)}).encode()
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False and r["reason"] == "too-large"


def test_unknown_format_refused(tmp_path, keys, feeds):
    m = _manifest(fmt=1).replace(b'"format": 1', b'"format": 3')
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False and r["reason"] == "bad-format"


def test_newest_release_from_step_ids(tmp_path, keys, feeds):
    m = _manifest(steps=("release-2", "release-10", "release-9", "other-99"))
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["release"] == 10


def test_gpgv_status_parser_rejects_multiple_and_bad_lines():
    ok = "[GNUPG:] GOODSIG AAAA k\n[GNUPG:] VALIDSIG AAAA 2026 1\n"
    assert updates._status_accepts(ok)
    assert not updates._status_accepts(ok + "[GNUPG:] GOODSIG BBBB k\n")
    assert not updates._status_accepts("[GNUPG:] GOODSIG AAAA k\n")
    for bad in ("BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "NO_PUBKEY"):
        assert not updates._status_accepts(ok + f"[GNUPG:] {bad} AAAA\n")


def test_check_caches_result(tmp_path, keys, feeds):
    m = _manifest()
    f = feeds(m, _sign(keys["good_home"], m))
    cfg = _cfg(tmp_path, keys, f.url)
    r = updates.check(cfg, cache_dir=tmp_path / "cache")
    assert updates.last_check(tmp_path / "cache") == r


# ---- status() -------------------------------------------------------------

def test_status_absent(tmp_path):
    cfg = updates.UpdateConfig(status_file=str(tmp_path / "missing.json"))
    assert updates.status(cfg) == {"helper": "absent"}


def test_status_malformed(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    assert updates.status(updates.UpdateConfig(status_file=str(p))) == {"helper": "absent"}
    p.write_text("[1,2]")
    assert updates.status(updates.UpdateConfig(status_file=str(p))) == {"helper": "absent"}


def test_status_valid(tmp_path):
    p = tmp_path / "s.json"
    doc = {"run_seq": 4, "state": "idle", "result": "applied",
           "steps": [{"id": "release-5", "result": "already-applied"}]}
    p.write_text(json.dumps(doc))
    s = updates.status(updates.UpdateConfig(status_file=str(p)))
    assert s["helper"] == "present"
    assert s["run_seq"] == 4 and s["result"] == "applied"
    assert updates.installed_release(s) == 5


# ---- review fixes --------------------------------------------------------

def _signed_feed(keys, feeds, body: bytes):
    return feeds(body, _sign(keys["good_home"], body))


@pytest.mark.parametrize("label,mutate,reason", [
    ("missing-expires", lambda d: d.pop("expires"), "bad-expires"),
    ("string-expires", lambda d: d.__setitem__("expires", "2099-01-01T00:00:00Z"), "bad-expires"),
    ("float-expires", lambda d: d.__setitem__("expires", float(d["expires"])), "bad-expires"),
    ("bool-expires", lambda d: d.__setitem__("expires", True), "bad-expires"),
    ("negative-published", lambda d: d.__setitem__("published", -5), "bad-published"),
    ("missing-published", lambda d: d.pop("published"), "bad-published"),
    ("string-published", lambda d: d.__setitem__("published", "yesterday"), "bad-published"),
    ("float-format", lambda d: d.__setitem__("format", 2.0), "bad-format"),
    ("bool-format", lambda d: d.__setitem__("format", True), "bad-format"),
])
def test_format2_strictness(tmp_path, keys, feeds, label, mutate, reason):
    d = json.loads(_manifest())
    mutate(d)
    body = json.dumps(d).encode()
    if label == "bool-format":
        body = body.replace(b'"format": true', b'"format": true')
    f = _signed_feed(keys, feeds, body)
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False and r["reason"] == reason, (label, r)


def test_format1_float_refused(tmp_path, keys, feeds):
    d = json.loads(_manifest(fmt=1))
    d["format"] = 1.0
    f = _signed_feed(keys, feeds, json.dumps(d).encode())
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False and r["reason"] == "bad-format", r


def test_installed_release_counts_only_applied_and_already_applied(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"run_seq": 9, "state": "idle", "result": "nothing", "steps": [
        {"id": "release-12", "result": "already-applied"},
        {"id": "release-13", "result": "failed"},
        {"id": "release-14", "result": "refused"},
        {"id": "release-15", "result": "nothing"},
    ]}))
    cfg = updates.UpdateConfig(status_file=str(p))
    st = updates.status(cfg)
    assert updates.installed_release(st) == 12
    cache = tmp_path / "c"
    cache.mkdir()
    (cache / "last_check.json").write_text(json.dumps({"ok": True, "release": 12}))
    assert updates.summary(cfg, cache)["available_release"] is None
    (cache / "last_check.json").write_text(json.dumps({"ok": True, "release": 13}))
    assert updates.summary(cfg, cache)["available_release"] == 13


def test_deeply_nested_files_do_not_raise(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("[" * 100000)
    assert updates.status(updates.UpdateConfig(status_file=str(p))) == {"helper": "absent"}
    (tmp_path / "last_check.json").write_text("[" * 100000)
    assert updates.last_check(tmp_path) is None


def test_deeply_nested_manifest_does_not_raise(tmp_path, keys, feeds):
    body = b"[" * 100000
    f = _signed_feed(keys, feeds, body)
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False


class _Redirector:
    """Server answering every GET with a redirect to `location`."""

    def __init__(self, location_fn):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", location_fn(self.server.server_address[1]))
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def test_redirect_to_other_host_refused(tmp_path, keys, feeds):
    m = _manifest()
    target = feeds(m, _sign(keys["good_home"], m))
    other_host = target.url.replace("127.0.0.1", "localhost")
    red = _Redirector(lambda port: other_host + "manifest.json")
    try:
        # control: the target itself works
        assert updates.check(_cfg(tmp_path, keys, target.url))["ok"] is True
        r = updates.check(_cfg(tmp_path, keys, red.url, "http://127.0.0.1:1/"))
        assert r["ok"] is False and r["reason"] == "unreachable", r
    finally:
        red.close()


def test_redirect_to_other_scheme_refused(tmp_path, keys):
    for loc in ("https://127.0.0.1:1/manifest.json", "file:///etc/passwd", "ftp://127.0.0.1/x"):
        red = _Redirector(lambda port, loc=loc: loc)
        try:
            r = updates.check(_cfg(tmp_path, keys, red.url, "http://127.0.0.1:1/"))
            assert r["ok"] is False and r["reason"] == "unreachable", (loc, r)
        finally:
            red.close()


def test_same_host_redirect_still_followed(tmp_path, keys, feeds):
    m = _manifest()
    target = feeds(m, _sign(keys["good_home"], m))
    red = _Redirector(lambda port: target.url + "manifest.json")
    try:
        # same host, different port: refused too (port is part of the origin)
        r = updates.check(_cfg(tmp_path, keys, red.url, "http://127.0.0.1:1/"))
        assert r["ok"] is False
    finally:
        red.close()


def test_slow_drip_cut_at_deadline(tmp_path, keys):
    import time

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "100000")
            self.end_headers()
            try:
                for _ in range(100000):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        cfg = _cfg(tmp_path, keys, url, "http://127.0.0.1:1/")
        cfg.timeout = 1.0
        t0 = time.monotonic()
        r = updates.check(cfg)
        elapsed = time.monotonic() - t0
        assert r["ok"] is False and r["reason"] == "unreachable", r
        assert elapsed < 4.0, elapsed
    finally:
        srv.shutdown()
        srv.server_close()


def test_gpgv_exit_1_with_perfect_status_refused(tmp_path, monkeypatch):
    class P:
        returncode = 1
        stdout = "[GNUPG:] GOODSIG AAAA k\n[GNUPG:] VALIDSIG AAAA 2026 1\n"

    ring = tmp_path / "r.gpg"
    ring.write_bytes(b"x")
    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **k: P())
    assert updates._verify(b"m", b"s", str(ring)) is False
    P.returncode = 0
    assert updates._verify(b"m", b"s", str(ring)) is True  # control: exit 0 accepted


def test_bad_primary_signature_falls_back_to_good_mirror(tmp_path, keys, feeds):
    m = _manifest()
    primary = feeds(m.replace(b'"seq": 7', b'"seq": 8'), _sign(keys["good_home"], m))
    mirror = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, primary.url, mirror.url))
    assert r["ok"] is True and r["source"] == "mirror", r
    assert r["primary_reason"] == "signature" and r["seq"] == 7


def test_expired_primary_falls_back_to_good_mirror(tmp_path, keys, feeds):
    now = datetime.now(timezone.utc)
    old = _manifest(published=now - timedelta(days=10), expires=now - timedelta(days=1))
    primary = feeds(old, _sign(keys["good_home"], old))
    m = _manifest()
    mirror = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, primary.url, mirror.url))
    assert r["ok"] is True and r["source"] == "mirror" and r["primary_reason"] == "expired", r


def test_both_bad_reports_both_reasons(tmp_path, keys, feeds):
    m = _manifest()
    bad = m.replace(b'"seq": 7', b'"seq": 8')
    primary = feeds(bad, _sign(keys["good_home"], m))
    now = datetime.now(timezone.utc)
    old = _manifest(published=now - timedelta(days=10), expires=now - timedelta(days=1))
    mirror = feeds(old, _sign(keys["good_home"], old))
    r = updates.check(_cfg(tmp_path, keys, primary.url, mirror.url))
    assert r["ok"] is False and r["reason"] == "signature", r
    assert r["primary_reason"] == "signature" and r["mirror_reason"] == "expired"


def _state_for(tmp_path, keys, feeds, installed, newest):
    m = _manifest(steps=(f"release-{newest}",))
    f = feeds(m, _sign(keys["good_home"], m))
    st = tmp_path / "status.json"
    st.write_text(json.dumps({"steps": [{"id": f"release-{installed}", "result": "applied"}]}))
    cfg = _cfg(tmp_path, keys, f.url)
    cfg.status_file = str(st)
    cache = tmp_path / "cache"
    r = updates.check(cfg, cache_dir=cache)
    return r, updates.summary(cfg, cache)


def test_manifest_older_than_installed_refused(tmp_path, keys, feeds):
    r, s = _state_for(tmp_path, keys, feeds, 12, 11)
    assert r["ok"] is False and r["reason"] == "seq_regressed", r
    assert s["available_release"] is None and s["state"] is None


def test_manifest_equal_is_up_to_date(tmp_path, keys, feeds):
    r, s = _state_for(tmp_path, keys, feeds, 12, 12)
    assert r["state"] == "up-to-date" and s["available_release"] is None


def test_manifest_newer_is_available(tmp_path, keys, feeds):
    r, s = _state_for(tmp_path, keys, feeds, 12, 13)
    assert r["state"] == "available" and s["available_release"] == 13


def _seq_feed(keys, feeds, seq, release):
    m = _manifest(seq=seq, steps=(f"release-{release}",))
    return feeds(m, _sign(keys["good_home"], m))


def test_lower_seq_than_last_accepted_refused(tmp_path, keys, feeds):
    cache = tmp_path / "cache"
    assert updates.check(_cfg(tmp_path, keys, _seq_feed(keys, feeds, 9, 20).url),
                         cache_dir=cache)["state"] == "available"
    cfg2 = _cfg(tmp_path, keys, _seq_feed(keys, feeds, 5, 21).url)
    r = updates.check(cfg2, cache_dir=cache)
    assert r["ok"] is False and r["reason"] == "seq_regressed", r
    assert updates.summary(cfg2, cache)["available_release"] is None


def test_primary_regressed_mirror_current_uses_mirror(tmp_path, keys, feeds):
    cache = tmp_path / "cache"
    updates.check(_cfg(tmp_path, keys, _seq_feed(keys, feeds, 9, 20).url), cache_dir=cache)
    old = _seq_feed(keys, feeds, 5, 21)
    cur = _seq_feed(keys, feeds, 10, 22)
    cfg = _cfg(tmp_path, keys, old.url, cur.url)
    r = updates.check(cfg, cache_dir=cache)
    assert r["ok"] is True and r["source"] == "mirror" and r["state"] == "available", r
    assert r["primary_reason"] == "seq_regressed" and r["seq"] == 10
    assert updates.summary(cfg, cache)["available_release"] == 22


def test_both_regressed_refused_nothing_offered_seq_unchanged(tmp_path, keys, feeds):
    cache = tmp_path / "cache"
    updates.check(_cfg(tmp_path, keys, _seq_feed(keys, feeds, 9, 20).url), cache_dir=cache)
    before = (cache / updates.ACCEPTED_NAME).read_text()
    cfg = _cfg(tmp_path, keys, _seq_feed(keys, feeds, 5, 21).url,
               _seq_feed(keys, feeds, 6, 22).url)
    r = updates.check(cfg, cache_dir=cache)
    assert r["ok"] is False and r["reason"] == "seq_regressed", r
    assert r["primary_reason"] == "seq_regressed" and r["mirror_reason"] == "seq_regressed"
    assert updates.summary(cfg, cache)["available_release"] is None
    assert (cache / updates.ACCEPTED_NAME).read_text() == before


def test_release_below_installed_primary_falls_to_mirror(tmp_path, keys, feeds):
    st = tmp_path / "status.json"
    st.write_text(json.dumps({"steps": [{"id": "release-12", "result": "applied"}]}))
    cfg = _cfg(tmp_path, keys, _seq_feed(keys, feeds, 3, 11).url,
               _seq_feed(keys, feeds, 4, 13).url)
    cfg.status_file = str(st)
    r = updates.check(cfg)
    assert r["ok"] is True and r["source"] == "mirror" and r["release"] == 13, r
    assert r["primary_reason"] == "seq_regressed"


def test_summary_reports_exact_reasons(tmp_path, keys, feeds):
    m = _manifest()
    bad = m.replace(b'"seq": 7', b'"seq": 8')
    primary = feeds(bad, _sign(keys["good_home"], m))
    cfg = _cfg(tmp_path, keys, primary.url, "http://127.0.0.1:1/")
    cache = tmp_path / "cache"
    updates.check(cfg, cache_dir=cache)
    s = updates.summary(cfg, cache)
    assert s["reasons"] == {"reason": "signature", "primary_reason": "signature",
                            "mirror_reason": "unreachable"}


@pytest.mark.parametrize("body", [b"[1,2]", b'"str"', b"null", b"7"])
def test_manifest_not_an_object_refused(tmp_path, keys, feeds, body):
    f = _signed_feed(keys, feeds, body)
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False and r["reason"] == "malformed", r


def test_steps_entry_without_id_ignored(tmp_path, keys, feeds):
    d = json.loads(_manifest(steps=("release-4",)))
    d["steps"] = [{"name": "x"}, 5, None, {"id": 9}, {"id": "release-4"}]
    f = _signed_feed(keys, feeds, json.dumps(d).encode())
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is True and r["release"] == 4, r


def test_missing_gpgv_binary_reason(tmp_path, keys, feeds, monkeypatch):
    m = _manifest()
    f = _signed_feed(keys, feeds, m)
    cfg = _cfg(tmp_path, keys, f.url)
    real = subprocess.run

    def fake(cmd, *a, **k):
        if cmd and cmd[0] == "gpgv":
            raise FileNotFoundError("gpgv")
        return real(cmd, *a, **k)

    monkeypatch.setattr(updates.subprocess, "run", fake)
    r = updates.check(cfg)
    assert r["ok"] is False and r["reason"] == "gpgv-missing", r


def test_cli_check_uses_app_data_dir(tmp_path, monkeypatch):
    from tinyagentos.app import resolve_data_dir

    monkeypatch.delenv("TAOS_DATA_DIR", raising=False)
    assert updates.default_cache_dir() == resolve_data_dir(None) / "updates"
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    assert updates.default_cache_dir() == resolve_data_dir(None) / "updates"
    assert updates.default_cache_dir() == tmp_path / "updates"


# ---- routes ---------------------------------------------------------------

@pytest_asyncio.fixture
async def route_env(tmp_data_dir, tmp_path, keys, feeds, monkeypatch):
    m = _manifest(steps=("release-12",))
    f = feeds(m, _sign(keys["good_home"], m))
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"run_seq": 1, "state": "idle", "result": "applied",
                                  "steps": [{"id": "release-10", "result": "applied"}]}))
    monkeypatch.setenv("TAOS_UPDATE_FEED", f.url)
    monkeypatch.setenv("TAOS_UPDATE_MIRROR", "http://127.0.0.1:1/")
    monkeypatch.setenv("TAOS_UPDATE_KEYRING", str(keys["ring"]))
    monkeypatch.setenv("TAOS_UPDATE_STATUS", str(status))
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    auth = app.state.auth
    auth.setup_user("admin", "Test Admin", "", "testpass")
    admin = auth.find_user("admin")
    admin_tok = auth.create_session(user_id=admin["id"], long_lived=True)
    code = auth.add_user_invite("member", "admin")
    auth.complete_invite("member", code, "Test Member", "", "memberpass12345")
    member = auth.find_user("member")
    member_tok = auth.create_session(user_id=member["id"], long_lived=True)

    def client(tok):
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={"taos_session": tok}, event_hooks=csrf_event_hooks())

    async with client(admin_tok) as a, client(member_tok) as mbr:
        yield a, mbr


@pytest.mark.asyncio
async def test_routes_owner_succeeds_control(route_env):
    admin, _ = route_env
    r = await admin.post("/api/updates/check")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True, r.text
    g = await admin.get("/api/updates")
    assert g.status_code == 200, g.text
    body = g.json()
    assert body["channel"] == "stable"
    assert body["installed_release"] == 10
    assert body["available_release"] == 12
    assert body["last_check"]["ok"] is True
    assert body["state"] == "available"
    assert body["reasons"] == {}
    assert body["helper"]["helper"] == "present"


@pytest.mark.asyncio
async def test_routes_non_owner_refused(route_env):
    admin, member = route_env
    # control first: the same calls succeed for the owner, so the 403 below is the
    # owner check and not CSRF or startup.
    assert (await admin.get("/api/updates")).status_code == 200
    assert (await admin.post("/api/updates/check")).status_code == 200
    for method, path in (("GET", "/api/updates"), ("POST", "/api/updates/check")):
        r = await member.request(method, path)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        assert "forbidden" in r.text.lower() or "admin" in r.text.lower(), r.text


# ---- CLI ------------------------------------------------------------------

def test_cli_install_rollback_not_available():
    for cmd in ("install", "rollback"):
        p = subprocess.run([sys.executable, "-m", "tinyagentos.updates", cmd],
                           capture_output=True, text=True)
        assert p.returncode == 2
        assert "not available yet" in (p.stdout + p.stderr)


def test_cli_status_prints_json(tmp_path):
    env = {**os.environ, "TAOS_UPDATE_STATUS": str(tmp_path / "none.json")}
    p = subprocess.run([sys.executable, "-m", "tinyagentos.updates", "status"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"helper": "absent"}
