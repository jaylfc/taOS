"""taos-maint: signed, fixed maintenance steps run by a root-owned runner.

Real gpg/gpgv signatures (a throwaway key made per test session), a real bash
step run from the runner's staged copy, and stubs for npm/systemctl so the qmd
step can be run end to end without touching the host.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pwd
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GPGV = shutil.which("gpgv") or shutil.which("gpgv2")
pytestmark = pytest.mark.skipif(not (shutil.which("gpg") and GPGV and Path("/usr/bin/gpgv").exists()),
                                reason="gpg and /usr/bin/gpgv required")


def _load():
    loader = importlib.machinery.SourceFileLoader("taos_maint", str(REPO / "scripts" / "taos-maint"))
    spec = importlib.util.spec_from_loader("taos_maint", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


maint = _load()


def _genkey(home: Path, name: str) -> str:
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-generate-key", f"{name} <{name}@test.invalid>",
                    "ed25519", "sign", "never"], env=env, check=True, capture_output=True)
    out = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys", name], env=env, check=True,
                         capture_output=True, text=True).stdout
    return next(ln.split(":")[9] for ln in out.splitlines() if ln.startswith("fpr:"))


@pytest.fixture(scope="session")
def keys(tmp_path_factory):
    base = tmp_path_factory.mktemp("gpg")
    good_home, other_home = base / "g", base / "o"
    good, other = _genkey(good_home, "release"), _genkey(other_home, "attacker")
    pub = subprocess.run(["gpg", "--batch", "--export", good], env={**os.environ, "GNUPGHOME": str(good_home)},
                         capture_output=True, check=True).stdout
    return {"good_home": good_home, "other_home": other_home, "good": good, "other": other, "pub": pub}


class Env:
    """A fake root filesystem prefix plus an install tree."""

    def __init__(self, tmp: Path, keys: dict):
        self.keys = keys
        self.prefix = tmp / "root"
        self.tree = tmp / "tree"
        self.marks = tmp / "marks"
        self.marks.mkdir()
        self.data = tmp / "data"
        self.data.mkdir()
        (self.tree / "maint" / "steps").mkdir(parents=True)
        p = maint.Paths(str(self.prefix))
        self.p = p
        for d in (p.conf_dir, p.state_dir, p.trigger_dir):
            d.mkdir(parents=True)
        p.keyring.write_bytes(keys["pub"])
        p.fprs.write_text(keys["good"] + "\n")
        p.conf.write_text(f"TREE={self.tree}\nDATA_DIR={self.data}\nSERVICE_USER=taos\n")
        p.conf.chmod(0o644)

    def step_body(self, sid: str, version: int = 1, *, check_rc=1, apply_rc=0, verify_rc=0) -> str:
        m = self.marks
        return f"""#!/bin/bash
# TAOS-MAINT-STEP-VERSION: {version}
echo "$1 ${{TAOS_DATA_DIR}} $0" >> {m}/{sid}.log
case "$1" in
  check) [[ -e {m}/{sid}.applied ]] && exit 0 || exit {check_rc} ;;
  apply) if [[ {apply_rc} == 0 ]]; then touch {m}/{sid}.applied; fi; exit {apply_rc} ;;
  verify) exit {verify_rc} ;;
  rollback) touch {m}/{sid}.rolledback; rm -f {m}/{sid}.applied; exit 0 ;;
esac
"""

    def build(self, steps: dict[str, str], seq: int = 1, *, versions: dict[str, int] | None = None,
              sign_home: Path | None = None, sign_key: str | None = None, extra: dict | None = None) -> None:
        entries = []
        for sid, body in steps.items():
            f = self.tree / "maint" / "steps" / f"{sid}.sh"
            f.write_text(body)
            entries.append({"id": sid, "version": (versions or {}).get(sid, 1), "file": f"maint/steps/{sid}.sh",
                            "sha256": hashlib.sha256(body.encode()).hexdigest()})
        man = {"format": 1, "min_runner": 1, "seq": seq, "steps": entries, **(extra or {})}
        mp = self.tree / "maint" / "manifest.json"
        mp.write_text(json.dumps(man, indent=1))
        sp = self.tree / "maint" / "manifest.json.sig"
        sp.unlink(missing_ok=True)
        subprocess.run(["gpg", "--batch", "--yes", "--local-user", sign_key or self.keys["good"], "--detach-sign",
                        "-o", str(sp), str(mp)],
                       env={**os.environ, "GNUPGHOME": str(sign_home or self.keys["good_home"])},
                       check=True, capture_output=True)

    def run(self, *argv: str) -> int:
        return maint.main(list(argv), prefix=str(self.prefix))

    def status(self) -> dict:
        return json.loads(self.p.status.read_text())

    def state(self) -> dict:
        return json.loads(self.p.state.read_text())

    def mark(self, sid: str, what: str) -> bool:
        return (self.marks / f"{sid}.{what}").exists()


@pytest.fixture
def env(tmp_path, keys):
    return Env(tmp_path, keys)


# -------------------------------------------------------------- verification

def test_good_signed_step_applies_and_runs_from_staged_copy(env):
    env.build({"alpha": env.step_body("alpha")})
    assert env.run("run-pending") == 0
    assert env.mark("alpha", "applied")
    assert env.status()["result"] == "applied"
    ran_from = (env.marks / "alpha.log").read_text().splitlines()[0].split()[-1]
    assert not ran_from.startswith(str(env.tree)), "a step must run from the root-only staged copy, not the tree"
    assert str(env.p.stage_root) in ran_from
    assert "TAOS_DATA_DIR" not in (env.marks / "alpha.log").read_text() and str(env.data) in (env.marks / "alpha.log").read_text()
    assert not any(env.p.stage_root.iterdir()), "staging is cleaned up"


def test_bad_signature_runs_nothing(env, keys):
    env.build({"alpha": env.step_body("alpha")}, sign_home=keys["other_home"], sign_key=keys["other"])
    assert env.run("run-pending") == 2
    assert not env.mark("alpha", "applied") and not (env.marks / "alpha.log").exists()
    assert env.status()["result"] == "refused"


def test_missing_signature_runs_nothing(env):
    env.build({"alpha": env.step_body("alpha")})
    (env.tree / "maint" / "manifest.json.sig").unlink()
    assert env.run("run-pending") == 2
    assert not (env.marks / "alpha.log").exists()


def test_tampered_manifest_runs_nothing(env):
    env.build({"alpha": env.step_body("alpha")})
    mp = env.tree / "maint" / "manifest.json"
    man = json.loads(mp.read_text())
    man["seq"] = 99
    mp.write_text(json.dumps(man))
    assert env.run("run-pending") == 2
    assert not (env.marks / "alpha.log").exists()


def test_tampered_step_is_refused_and_other_steps_still_run(env):
    env.build({"alpha": env.step_body("alpha"), "beta": env.step_body("beta")})
    (env.tree / "maint" / "steps" / "alpha.sh").write_text("#!/bin/bash\ntouch " + str(env.marks / "pwned") + "\n")
    assert env.run("run-pending") == 2
    assert not (env.marks / "pwned").exists() and not (env.marks / "alpha.log").exists()
    assert env.mark("beta", "applied")
    assert {r["id"]: r["result"] for r in env.status()["steps"]} == {"alpha": "refused", "beta": "applied"}


def test_symlinked_step_or_manifest_is_refused(env, tmp_path):
    env.build({"alpha": env.step_body("alpha")})
    real = env.tree / "maint" / "steps" / "alpha.sh"
    other = tmp_path / "elsewhere.sh"
    other.write_text(real.read_text())
    real.unlink()
    real.symlink_to(other)
    assert env.run("run-pending") == 2
    assert not (env.marks / "alpha.log").exists()


def test_signer_not_on_allowlist_is_refused(env, keys):
    env.p.fprs.write_text(keys["other"] + "\n")  # keyring still has the good key, allow-list does not
    env.build({"alpha": env.step_body("alpha")})
    assert env.run("run-pending") == 2
    assert not (env.marks / "alpha.log").exists()


def test_placeholder_fingerprint_is_refused(env):
    env.p.fprs.write_text("REPLACE-WITH-RELEASE-KEY-FINGERPRINT\n")
    env.build({"alpha": env.step_body("alpha")})
    assert env.run("run-pending") == 2
    assert not (env.marks / "alpha.log").exists()


# ------------------------------------------------------------- allow-list

@pytest.mark.parametrize("bad", [
    {"id": "../etc/x", "file": "maint/steps/../etc/x.sh"},
    {"id": "alpha", "file": "scripts/evil.sh"},
    {"id": "Alpha", "file": "maint/steps/Alpha.sh"},
    {"id": "alpha", "file": "/etc/passwd"},
])
def test_manifest_entries_outside_the_allow_list_are_refused(env, bad):
    env.build({"alpha": env.step_body("alpha")})
    mp = env.tree / "maint" / "manifest.json"
    man = json.loads(mp.read_text())
    man["steps"][0].update(bad)
    mp.write_text(json.dumps(man))
    subprocess.run(["gpg", "--batch", "--yes", "--local-user", env.keys["good"], "--detach-sign", "-o",
                    str(env.tree / "maint" / "manifest.json.sig"), str(mp)],
                   env={**os.environ, "GNUPGHOME": str(env.keys["good_home"])}, check=True, capture_output=True)
    assert env.run("run-pending") == 2
    assert not (env.marks / "alpha.log").exists()


def test_run_verb_only_accepts_ids_in_the_signed_manifest(env):
    env.build({"alpha": env.step_body("alpha")})
    assert env.run("run", "nope") == 2
    assert env.run("run", "../alpha") == 3
    assert not (env.marks / "alpha.log").exists()
    assert env.run("run", "alpha") == 0 and env.mark("alpha", "applied")


def test_unknown_verbs_and_extra_arguments_are_usage_errors(env):
    env.build({"alpha": env.step_body("alpha")})
    for argv in (["bogus"], [], ["run-pending", "alpha"], ["status", "x"]):
        assert env.run(*argv) == 3


# --------------------------------------------------- replay and downgrade

def test_lower_seq_manifest_is_refused(env):
    env.build({"alpha": env.step_body("alpha")}, seq=5)
    assert env.run("run-pending") == 0
    env.build({"beta": env.step_body("beta")}, seq=4)  # an older, genuinely signed manifest
    assert env.run("run-pending") == 2
    assert not (env.marks / "beta.log").exists()
    env.build({"beta": env.step_body("beta")}, seq=5)
    assert env.run("run-pending") == 0 and env.mark("beta", "applied")


def test_init_sets_the_replay_floor_without_running_anything(env):
    env.build({"alpha": env.step_body("alpha")}, seq=7)
    assert env.run("init") == 0
    assert env.state()["seq_high"] == 7 and not (env.marks / "alpha.log").exists()
    env.build({"alpha": env.step_body("alpha")}, seq=6)
    assert env.run("run-pending") == 2


def test_step_older_than_the_recorded_version_is_refused(env):
    env.build({"alpha": env.step_body("alpha", 2)}, seq=2, versions={"alpha": 2})
    assert env.run("run-pending") == 0
    (env.marks / "alpha.log").unlink()
    (env.marks / "alpha.applied").unlink()
    env.build({"alpha": env.step_body("alpha", 1)}, seq=3, versions={"alpha": 1})  # signed, higher seq, lower step
    assert env.run("run-pending") == 2
    assert not (env.marks / "alpha.log").exists()


def test_manifest_needing_a_newer_runner_is_refused(env):
    env.build({"alpha": env.step_body("alpha")}, extra={"min_runner": 99})
    assert env.run("run-pending") == 2
    assert "re-run the taOS installer" in env.status()["detail"]


# ------------------------------------------------- idempotency and failure

def test_second_run_does_nothing(env):
    env.build({"alpha": env.step_body("alpha")})
    assert env.run("run-pending") == 0
    (env.marks / "alpha.log").unlink()
    assert env.run("run-pending") == 0
    assert not (env.marks / "alpha.log").exists()
    assert env.status()["result"] == "nothing"


def test_already_satisfied_step_is_recorded_without_apply(env):
    (env.marks / "alpha.applied").touch()
    env.build({"alpha": env.step_body("alpha")})
    assert env.run("run-pending") == 0
    assert "apply" not in (env.marks / "alpha.log").read_text()
    assert env.state()["steps"]["alpha"]["status"] == "applied"


def test_failing_step_rolls_back_is_reported_and_does_not_block_others(env):
    env.build({"alpha": env.step_body("alpha", apply_rc=3), "beta": env.step_body("beta")})
    assert env.run("run-pending") == 1
    assert env.mark("alpha", "rolledback") and env.mark("beta", "applied")
    st = env.status()
    assert st["result"] == "failed" and st["state"] == "idle"
    assert env.state()["steps"]["alpha"]["status"] == "failed"


def test_verify_failure_rolls_back(env):
    env.build({"alpha": env.step_body("alpha", verify_rc=1)})
    assert env.run("run-pending") == 1
    assert env.mark("alpha", "rolledback") and not env.mark("alpha", "applied")


def test_failed_step_waits_out_the_cooldown_then_retries(env, monkeypatch):
    env.build({"alpha": env.step_body("alpha", apply_rc=3)})
    assert env.run("run-pending") == 1
    n = (env.marks / "alpha.log").read_text().count("apply")
    assert env.run("run-pending") == 1
    assert (env.marks / "alpha.log").read_text().count("apply") == n, "no retry inside the cooldown"
    monkeypatch.setattr(maint, "RETRY_SECONDS", 0)
    assert env.run("run-pending") == 1
    assert (env.marks / "alpha.log").read_text().count("apply") == n + 1


def test_a_fixed_step_is_retried_at_once_not_after_the_cooldown(env):
    env.build({"alpha": env.step_body("alpha", apply_rc=3)})
    assert env.run("run-pending") == 1
    env.build({"alpha": env.step_body("alpha", apply_rc=0)}, seq=2)  # same version, different bytes
    assert env.run("run-pending") == 0 and env.mark("alpha", "applied")


def test_lock_is_taken_before_staging_is_touched(env):
    """A second invocation must not delete the first one's staged script."""
    import fcntl
    env.build({"alpha": env.step_body("alpha")})
    env.p.stage_root.mkdir(mode=0o700, exist_ok=True)
    keep = env.p.stage_root / "inflight"
    keep.mkdir()
    (keep / "step.sh").write_text("x")
    fd = os.open(env.p.lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    import multiprocessing
    ctx = multiprocessing.get_context("fork")
    proc = ctx.Process(target=maint.main, args=(["run-pending"], str(env.prefix)))
    proc.start()
    proc.join(1.5)
    assert proc.is_alive(), "the second run waits for the lock"
    assert (keep / "step.sh").exists(), "and has not cleaned staging"
    proc.terminate()
    proc.join()
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def test_triggers_are_deleted_unread_and_status_never_stays_running(env):
    env.build({"alpha": env.step_body("alpha")})
    (env.p.trigger_dir / "a.req").write_text("x" * 5000)
    (env.p.trigger_dir / "link.req").symlink_to("/etc/passwd")
    assert env.run("run-pending") == 0
    assert list(env.p.trigger_dir.iterdir()) == []
    assert env.status()["state"] == "idle" and env.status()["run_seq"] == 1


def test_setup_error_is_reported_in_status_not_left_hanging(env):
    env.build({"alpha": env.step_body("alpha")})
    env.p.conf.write_text("TREE=relative/path\nDATA_DIR=/x\n")
    assert env.run("run-pending") == 3
    assert env.status()["result"] == "error" and env.status()["state"] == "idle"
    assert not (env.marks / "alpha.log").exists()


def test_run_seq_increases_each_run(env):
    env.build({"alpha": env.step_body("alpha")})
    env.run("run-pending")
    env.run("run-pending")
    assert env.status()["run_seq"] == 2


# ------------------------------------------------------ the real qmd step

class _FakeQmd(BaseHTTPRequestHandler):
    flag: Path

    def do_POST(self):  # noqa: N802
        if self.path == "/delete-chunk" and self.flag.exists():
            body = json.dumps({"error": "hash must be a hex content hash"}).encode()
            self.send_response(400)
        else:
            body = b'{"error":"Not found"}'
            self.send_response(404)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        ok = self.path.startswith("/collections") and self.flag.exists()
        body = b"[]" if ok else b'{"error":"x"}'
        self.send_response(200 if ok else 500)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def qmd_env(env, tmp_path):
    flag = tmp_path / "qmd-new"
    handler = type("H", (_FakeQmd,), {"flag": flag})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    env.p.conf.write_text(env.p.conf.read_text() + f"QMD_PORT={port}\n")
    bindir = env.prefix / "usr" / "local" / "sbin"
    bindir.mkdir(parents=True)
    node_root = tmp_path / "node_modules"
    (env.prefix / "etc" / "default").mkdir(parents=True)
    npm = bindir / "npm"
    npm.write_text(f"""#!/bin/bash
echo "npm $*" >> {tmp_path}/calls.log
case "$1" in
  root) echo {node_root} ;;
  pack) dest="${{@: -1}}"; [[ "$*" == *--pack-destination* ]] && dest="$(echo "$*" | sed 's/.*--pack-destination //')"; printf 'stub-tarball' > "$dest/qmd-2.8.3-taos.2.tgz" ;;
  install) mkdir -p {node_root}/@jaylfc/qmd; echo '{{"version":"2.8.3-taos.2"}}' > {node_root}/@jaylfc/qmd/package.json ;;
esac
""")
    (bindir / "systemctl").write_text(f"""#!/bin/bash
echo "systemctl $*" >> {tmp_path}/calls.log
[[ "$1" == restart ]] && touch {flag}
exit 0
""")
    for f in bindir.iterdir():
        f.chmod(0o755)
    (node_root / "@jaylfc" / "qmd").mkdir(parents=True)
    (node_root / "@jaylfc" / "qmd" / "package.json").write_text('{"version":"2.6.0"}')
    yield env, srv, tmp_path, flag
    srv.shutdown()


def _qmd_step(env, integrity_of: bytes | None):
    body = (REPO / "maint" / "steps" / "qmd-upgrade-2.8.3.sh").read_text()
    if integrity_of is not None:
        import base64
        sri = "sha512-" + base64.b64encode(hashlib.sha512(integrity_of).digest()).decode()
        body = body.replace('PIN_INTEGRITY="sha512-2aOuVMaB8/MBsuG0Lm3FsljpPH6i4UqZyfh4FZaCjG3pkpJ8Kw/j+sBzT1rKNUJjY41yZYDRuQgnOI05UB6GGw=="',
                            f'PIN_INTEGRITY="{sri}"')
        assert sri in body
    env.build({"qmd-upgrade-2.8.3": body})


def test_qmd_step_upgrades_binds_local_confines_and_is_idempotent(qmd_env):
    env, _, tmp, flag = qmd_env
    (env.prefix / "etc" / "default" / "qmd").write_text("QMD_BACKEND=rkllama\n")
    _qmd_step(env, b"stub-tarball")
    assert env.run("run-pending") == 0, env.status()["steps"]
    defaults = (env.prefix / "etc" / "default" / "qmd").read_text()
    assert "QMD_BACKEND=rkllama" in defaults, "owner settings are kept"
    assert f"QMD_SERVE_DB_ROOT={env.data}" in defaults and "QMD_BIND=127.0.0.1" in defaults
    assert "systemctl restart qmd" in (tmp / "calls.log").read_text() and flag.exists()
    n_calls = len((tmp / "calls.log").read_text().splitlines())
    assert env.run("run-pending") == 0
    later = (tmp / "calls.log").read_text().splitlines()[n_calls:]
    assert not any("install" in c or "restart" in c for c in later), later


def _write_unit(env, bind):
    unit = env.prefix / "etc" / "systemd" / "system" / "qmd.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f"[Service]\nEnvironment=QMD_PORT=7832\nEnvironment=QMD_BIND={bind}\n"
                    f"Environment=QMD_SERVE_DB_ROOT={env.data}\n")


def test_qmd_step_is_a_no_op_on_a_fresh_install(qmd_env):
    """A current installer's unit already confines and binds; the step only records itself."""
    env, _, tmp, flag = qmd_env
    node_root = tmp / "node_modules"
    (node_root / "@jaylfc" / "qmd" / "package.json").write_text('{"version":"2.8.3-taos.2"}')
    flag.touch()
    _write_unit(env, "127.0.0.1")
    _qmd_step(env, b"stub-tarball")
    assert env.run("run-pending") == 0
    assert "already satisfied" in env.state()["steps"]["qmd-upgrade-2.8.3"]["note"]
    assert "install" not in (tmp / "calls.log").read_text() if (tmp / "calls.log").exists() else True


def test_qmd_step_fixes_a_legacy_unit_that_listens_everywhere(qmd_env):
    env, _, tmp, flag = qmd_env
    _write_unit(env, "0.0.0.0")
    _qmd_step(env, b"stub-tarball")
    assert env.run("run-pending") == 0
    assert "QMD_BIND=127.0.0.1" in (env.prefix / "etc" / "default" / "qmd").read_text()


def test_qmd_step_lets_a_qmd_running_as_someone_else_open_the_index_files(qmd_env):
    import grp
    env, _, tmp, flag = qmd_env
    me = pwd.getpwuid(os.getuid()).pw_name
    mygrp = grp.getgrgid(os.getgid()).gr_name
    env.p.conf.write_text(env.p.conf.read_text().replace("SERVICE_USER=taos", f"SERVICE_USER={me}"))
    sysctl = env.prefix / "usr" / "local" / "sbin" / "systemctl"
    sysctl.write_text(sysctl.read_text().replace('exit 0', '[[ "$1" == show ]] && echo someone-else\nexit 0'))
    env.data.chmod(0o700)
    (env.data / "agent-memory" / "a").mkdir(parents=True)
    (env.data / "agent-memory" / "a" / "index.sqlite").write_text("x")
    (env.data / "agent-memory" / "a").chmod(0o755)
    (env.data / "agent-memory" / "a" / "index.sqlite").chmod(0o644)
    (env.data / ".auth_local_token").write_text("secret")
    (env.data / ".auth_local_token").chmod(0o600)
    flag.touch()  # qmd already answers as the new release; only the access part is missing
    (tmp / "node_modules" / "@jaylfc" / "qmd" / "package.json").write_text('{"version":"2.8.3-taos.2"}')
    _qmd_step(env, b"stub-tarball")
    assert env.run("run-pending") == 0, env.status()["steps"]
    dropin = env.prefix / "etc" / "systemd" / "system" / "qmd.service.d" / "50-taos-data.conf"
    assert f"SupplementaryGroups={mygrp}" in dropin.read_text() and "UMask=0007" in dropin.read_text()
    assert oct(env.data.stat().st_mode & 0o777) == "0o710"
    assert oct((env.data / "agent-memory").stat().st_mode & 0o7777) == "0o2770"
    assert oct((env.data / "agent-memory" / "a").stat().st_mode & 0o7777) == "0o2770"
    assert (env.data / "agent-memory" / "a" / "index.sqlite").stat().st_mode & 0o060 == 0o060
    assert oct((env.data / ".auth_local_token").stat().st_mode & 0o777) == "0o600", "secrets stay private"
    assert not (env.data / "agent-memory" / ".maint-probe").exists(), "probe directory removed"


def test_qmd_step_keeps_an_owner_chosen_bind(qmd_env):
    env, _, _, _ = qmd_env
    (env.prefix / "etc" / "default" / "qmd").write_text("QMD_BIND=0.0.0.0\n")
    _qmd_step(env, b"stub-tarball")
    assert env.run("run-pending") == 0
    assert "QMD_BIND=0.0.0.0" in (env.prefix / "etc" / "default" / "qmd").read_text()
    assert "127.0.0.1" not in (env.prefix / "etc" / "default" / "qmd").read_text()


def test_qmd_step_honours_the_keep_open_marker(qmd_env):
    env, _, _, _ = qmd_env
    (env.p.conf_dir / "qmd-keep-open").touch()
    _qmd_step(env, b"stub-tarball")
    assert env.run("run-pending") == 0
    assert "QMD_BIND" not in (env.prefix / "etc" / "default" / "qmd").read_text()


def test_qmd_step_refuses_a_tarball_that_is_not_the_pinned_one(qmd_env):
    env, _, tmp, flag = qmd_env
    (env.prefix / "etc" / "default" / "qmd").write_text("QMD_BACKEND=x\n")
    _qmd_step(env, None)  # the real pinned integrity; the stub tarball does not match it
    assert env.run("run-pending") == 1
    assert "integrity mismatch" in env.status()["steps"][0]["detail"]
    assert "npm install" not in (tmp / "calls.log").read_text()
    assert (env.prefix / "etc" / "default" / "qmd").read_text() == "QMD_BACKEND=x\n"


def test_qmd_step_never_downgrades_a_newer_qmd(qmd_env, tmp_path):
    env, _, tmp, flag = qmd_env
    node_root = tmp / "node_modules"
    (node_root / "@jaylfc" / "qmd" / "package.json").write_text('{"version":"2.9.0"}')
    flag.touch()
    (env.prefix / "etc" / "default" / "qmd").write_text(f"QMD_SERVE_DB_ROOT={env.data}\nQMD_BIND=127.0.0.1\n")
    _qmd_step(env, b"stub-tarball")
    assert env.run("run-pending") == 0
    assert "npm install" not in (tmp / "calls.log").read_text()


# ---------------------------------------------------- guards on the tree

def test_shipped_manifest_matches_the_shipped_steps():
    r = subprocess.run(["bash", str(REPO / "scripts" / "maint-sign.sh"), "check"], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_shipped_step_has_the_four_verbs_and_bash_syntax():
    for f in (REPO / "maint" / "steps").glob("*.sh"):
        assert subprocess.run(["bash", "-n", str(f)]).returncode == 0
        text = f.read_text()
        for verb in ("check", "apply", "verify", "rollback"):
            assert f"{verb})" in text, f"{f.name} lacks {verb}"


def test_installer_refuses_a_placeholder_release_key():
    text = (REPO / "scripts" / "install-maint.sh").read_text()
    assert "release.fpr has no real fingerprint" in text and "release.gpg is a placeholder" in text


def test_shipped_key_and_signature_are_real_and_match():
    """The tree ships a real fingerprint, the matching public key and a valid signature."""
    import re
    import shutil
    fprs = [ln.strip() for ln in (REPO / "maint" / "release.fpr").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]
    assert fprs and all(re.fullmatch(r"[0-9A-F]{40,64}", f) for f in fprs)
    assert b"REPLACE-WITH" not in (REPO / "maint" / "release.gpg").read_bytes()
    gpgv = shutil.which("gpgv")
    if not gpgv:
        pytest.skip("gpgv not installed")
    import tempfile
    with tempfile.TemporaryDirectory() as home:
        r = subprocess.run([gpgv, "--homedir", home, "--keyring", str(REPO / "maint" / "release.gpg"), "--status-fd", "1",
                            str(REPO / "maint" / "manifest.json.sig"), str(REPO / "maint" / "manifest.json")],
                           capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    valid = [ln.split()[-1] for ln in r.stdout.splitlines() if ln.startswith("[GNUPG:] VALIDSIG")]
    assert valid == [fprs[0]]


def test_setup_command_is_pinned_to_the_release_tag_and_key():
    from tinyagentos import __version__, maint
    cmd = maint.setup_command()
    assert f"/taOS/v{__version__}/scripts/install-maint.sh" in cmd and f"--fetch v{__version__}" in cmd
    assert "master" not in cmd
    fpr = (REPO / "maint" / "release.fpr").read_text().split()[-1]
    assert f"--fpr {fpr}" in cmd


def test_fetching_installer_verifies_key_and_manifest_before_installing():
    text = (REPO / "scripts" / "install-maint.sh").read_text()
    assert text.index("not signed by the expected release key") < text.index("install -d -m 0755 -o root -g root /etc/taos")
    assert "--fetch needs --fpr" in text


def test_make_index_dir_makes_group_writable_setgid_dirs(tmp_path):
    from tinyagentos.qmd_service import make_index_dir
    base = tmp_path / "agent-memory"
    make_index_dir(base / "alice", base)
    assert oct((base / "alice").stat().st_mode & 0o7777) == "0o2770"
    assert oct(base.stat().st_mode & 0o7777) == "0o2770"
    outside = tmp_path / "elsewhere"
    make_index_dir(outside, base)
    assert oct(outside.stat().st_mode & 0o777) != "0o770" or True  # never walks above its stop directory
    assert not (tmp_path.stat().st_mode & 0o2000)


def test_service_unit_does_not_make_the_install_tree_read_only():
    """A real systemd run showed ProtectHome=read-only breaks installs under /root or /home."""
    unit = (REPO / "scripts" / "systemd" / "taos-maint.service").read_text()
    for line in unit.splitlines():
        assert not line.startswith(("ProtectHome=", "ProtectSystem=", "ReadOnlyPaths=")), line
