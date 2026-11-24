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


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _manifest(fmt=2, seq=7, steps=("base-1", "release-3", "release-12", "release-9"),
              published=None, expires=None) -> bytes:
    now = datetime.now(timezone.utc)
    published = published or now - timedelta(days=1)
    m = {"format": fmt, "seq": seq, "published": _iso(published),
         "steps": [{"id": s} for s in steps]}
    if fmt == 2:
        m["expires"] = _iso(expires or published + timedelta(days=14))
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
    m = json.dumps({"format": 1, "seq": 1, "published": "2026-01-01T00:00:00Z",
                    "steps": [], "pad": "x" * (1024 * 1024)}).encode()
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False and r["reason"] == "too-large"


def test_unknown_format_refused(tmp_path, keys, feeds):
    m = _manifest(fmt=1).replace(b'"format": 1', b'"format": 3')
    f = feeds(m, _sign(keys["good_home"], m))
    r = updates.check(_cfg(tmp_path, keys, f.url))
    assert r["ok"] is False and r["reason"] == "malformed"


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
           "steps": [{"id": "release-5", "result": "applied"}]}
    p.write_text(json.dumps(doc))
    s = updates.status(updates.UpdateConfig(status_file=str(p)))
    assert s["helper"] == "present"
    assert s["run_seq"] == 4 and s["result"] == "applied"
    assert updates.installed_release(s) == 5


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
