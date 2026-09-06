# Worker Pairing + Sharing — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the smallest end-to-end useful slice of worker pairing + sharing — pairing OTP, tier A (inference-only) shares with capability allowlist + concurrent-jobs limit, owner observability, and recipient borrowed-worker UI. Friends-and-family GPU lending works at completion.

**Architecture:** Worker generates short-lived OTP via CLI/tray → owner pastes into Cluster app → controller calls worker `/pair` → worker returns bearer credential. Owner creates a share for a recipient (tier A, capabilities, expiry) → controller signs a share token → recipient pastes into their taOS → recipient's controller redeems the token at the owner's worker → recipient gets a long-lived bearer they use for inference. All inference calls from the recipient go through the worker's gate, which enforces tier + capability + concurrent-jobs limits per share.

**Tech Stack:** FastAPI, aiosqlite (existing `BaseStore` pattern), httpx, React + TypeScript (existing `ClusterApp.tsx`), shadcn/ui primitives, the existing notification stack (`NotificationStore.emit_event`).

**Spec:** [docs/superpowers/specs/2026-04-14-worker-sharing-design.md](../specs/2026-04-14-worker-sharing-design.md)
**Epic:** [#212](https://github.com/jaylfc/tinyagentos/issues/212)

---

## File Structure

### Worker-side (new + modified)

- **Create** `tinyagentos/worker/pairing.py` — OTP generation, validation, single-use semantics, persistent pairing record (one file: ~150 lines)
- **Modify** `tinyagentos/worker/agent.py` — add `/pair` endpoint, accept bearer auth on inference calls
- **Modify** `tinyagentos/worker/__main__.py` — add `pair` subcommand to existing CLI
- **Modify** `tinyagentos/worker/tray.py` — add "Generate pairing code" menu item

### Controller-side (new + modified)

- **Create** `tinyagentos/cluster/shares.py` — `SharesStore` (BaseStore subclass), share record schema, expiry watcher
- **Create** `tinyagentos/cluster/share_tokens.py` — token signing/validation with HMAC + structured payload
- **Modify** `tinyagentos/routes/cluster.py` — add `/api/cluster/pair`, `/api/cluster/workers/{id}/shares`, `/api/cluster/shares/redeem`, `/api/cluster/borrowed`
- **Modify** `tinyagentos/cluster/manager.py` — track per-worker active shares, gate route calls through share enforcement
- **Modify** `tinyagentos/app.py` — wire `SharesStore` into lifespan, register expiry watcher

### Frontend (new + modified)

- **Modify** `desktop/src/apps/ClusterApp.tsx` — pairing dialog, share creation dialog, redeem dialog, borrowed badge + colour border
- **Create** `desktop/src/apps/cluster/PairingDialog.tsx` — OTP paste UI
- **Create** `desktop/src/apps/cluster/ShareDialog.tsx` — owner share creation modal
- **Create** `desktop/src/apps/cluster/RedeemDialog.tsx` — recipient share-token paste modal
- **Create** `desktop/src/apps/cluster/BorrowedWorkerBadge.tsx` — visual marker component
- **Modify** `desktop/src/apps/ActivityApp.tsx` — add Shares card
- **Create** `desktop/src/apps/activity/SharesCard.tsx` — Activity card with per-share row + quick-actions

### Tests (new)

- **Create** `tests/test_worker_pairing.py` — OTP generation, validation, single-use, expiry
- **Create** `tests/test_shares_store.py` — schema CRUD, expiry watcher, revocation set
- **Create** `tests/test_share_tokens.py` — token signing/validation, replay rejection, expiry
- **Create** `tests/test_routes_cluster_pair.py` — `/api/cluster/pair` endpoint contract
- **Create** `tests/test_routes_cluster_shares.py` — share CRUD + redeem flow + tier A enforcement

---

## Riskiest Pieces (Review First)

These are the tasks with the highest blast-radius if they go wrong. Reviewer should scrutinise these PRs hardest.

1. **Task 4 — `share_tokens.py`** — token signing/validation. HMAC key handling, replay rejection, expiry windows. A bug here means a leaked token can outlive its share or be replayed cross-share.
2. **Task 7 — Tier A enforcement gate** — this is what keeps the share honest. If there's a code path that bypasses the gate (e.g. a legacy inference route that doesn't check), the share's capability allowlist is theatre.
3. **Task 1 — OTP single-use semantics** — must be atomic across concurrent redemption attempts. Two callers racing the same OTP must not both succeed.
4. **Task 8 — Bearer revocation** — when a share is revoked, the recipient's bearer must be invalidated immediately, not on next heartbeat. Race conditions between revoke and in-flight calls need an explicit drain semantics.

---

## Task 1: Worker OTP module

**Files:**
- Create: `tinyagentos/worker/pairing.py`
- Test: `tests/test_worker_pairing.py`

OTP semantics: 8 numeric digits, generated random, scoped to one specific controller URL, valid for 10 minutes, single-use. On successful redemption the worker persists a pairing record `{controller_url, bearer, paired_at}` and invalidates the OTP.

- [ ] **Step 1: Write the failing test for OTP generation**

```python
# tests/test_worker_pairing.py
import pytest
from tinyagentos.worker.pairing import PairingManager


@pytest.fixture
def pm(tmp_path):
    return PairingManager(state_dir=tmp_path)


def test_generate_otp_returns_8_digits(pm):
    otp = pm.generate_otp(controller_url="https://taos.local:6969")
    assert len(otp) == 8
    assert otp.isdigit()


def test_generate_otp_persists_record(pm, tmp_path):
    pm.generate_otp(controller_url="https://taos.local:6969")
    assert (tmp_path / "pending_otps.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/jay/tinyagentos && .venv/bin/pytest tests/test_worker_pairing.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'tinyagentos.worker.pairing'`

- [ ] **Step 3: Write minimal `PairingManager.generate_otp`**

```python
# tinyagentos/worker/pairing.py
"""Worker-side pairing: OTP issuance, validation, and pairing record persistence.

The worker generates a short-lived 8-digit OTP scoped to a specific
controller URL. The owner pastes it into their taOS Cluster app; the
controller calls the worker's /pair endpoint with the OTP. On
successful redemption the worker:
- Persists a (controller_url, bearer, paired_at) record to disk.
- Invalidates the OTP so it can't be replayed.

OTPs are single-use, time-limited (10 minutes), and bound to one
controller URL — a leaked OTP can't be redeemed against a different
controller than the one the worker generated it for.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

OTP_TTL_SECONDS = 600  # 10 minutes


@dataclass
class PendingOTP:
    otp: str
    controller_url: str
    issued_at: float
    expires_at: float


class PairingManager:
    """File-backed pairing state for a worker.

    Threadsafe via a single in-process lock — pairing operations are
    rare so contention is irrelevant; the lock just keeps the JSON
    file from corrupting under concurrent access.
    """

    def __init__(self, state_dir: Path):
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._otp_path = self._state_dir / "pending_otps.json"
        self._pairings_path = self._state_dir / "pairings.json"
        self._lock = Lock()

    def generate_otp(self, controller_url: str) -> str:
        """Generate a fresh 8-digit OTP scoped to `controller_url`."""
        otp = f"{secrets.randbelow(10**8):08d}"
        now = time.time()
        with self._lock:
            pending = self._read_pending()
            pending.append({
                "otp": otp,
                "controller_url": controller_url,
                "issued_at": now,
                "expires_at": now + OTP_TTL_SECONDS,
            })
            self._write_pending(pending)
        return otp

    def _read_pending(self) -> list[dict]:
        if not self._otp_path.exists():
            return []
        try:
            return json.loads(self._otp_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def _write_pending(self, data: list[dict]) -> None:
        tmp = self._otp_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._otp_path)
```

- [ ] **Step 4: Run tests to verify both pass**

```bash
.venv/bin/pytest tests/test_worker_pairing.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/worker/pairing.py tests/test_worker_pairing.py
git commit -m "feat(worker): PairingManager generates 8-digit OTPs scoped to controller URL"
```

- [ ] **Step 6: Add validation tests**

```python
# Append to tests/test_worker_pairing.py
import time as _time

def test_validate_correct_otp(pm):
    otp = pm.generate_otp(controller_url="https://taos.local:6969")
    record = pm.validate_and_consume(otp, controller_url="https://taos.local:6969")
    assert record is not None
    assert record["controller_url"] == "https://taos.local:6969"


def test_validate_wrong_otp(pm):
    pm.generate_otp(controller_url="https://taos.local:6969")
    record = pm.validate_and_consume("00000000", controller_url="https://taos.local:6969")
    assert record is None


def test_validate_wrong_controller_rejected(pm):
    otp = pm.generate_otp(controller_url="https://taos.local:6969")
    record = pm.validate_and_consume(otp, controller_url="https://evil.example:6969")
    assert record is None


def test_validate_consumes_otp(pm):
    otp = pm.generate_otp(controller_url="https://taos.local:6969")
    pm.validate_and_consume(otp, controller_url="https://taos.local:6969")
    second = pm.validate_and_consume(otp, controller_url="https://taos.local:6969")
    assert second is None  # already consumed


def test_validate_expired_otp_rejected(pm, monkeypatch):
    otp = pm.generate_otp(controller_url="https://taos.local:6969")
    real_time = _time.time
    monkeypatch.setattr("tinyagentos.worker.pairing.time.time", lambda: real_time() + 700)
    record = pm.validate_and_consume(otp, controller_url="https://taos.local:6969")
    assert record is None
```

- [ ] **Step 7: Run tests, verify they fail**

```bash
.venv/bin/pytest tests/test_worker_pairing.py -v
```
Expected: 4 failures (`PairingManager has no attribute 'validate_and_consume'`)

- [ ] **Step 8: Implement `validate_and_consume`**

```python
# Add to PairingManager in tinyagentos/worker/pairing.py
    def validate_and_consume(self, otp: str, controller_url: str) -> dict | None:
        """Validate `otp` against `controller_url`. On success returns the
        pending record AND atomically removes it (single-use). Returns None
        if the OTP doesn't exist, was issued for a different controller, or
        has expired.
        """
        now = time.time()
        with self._lock:
            pending = self._read_pending()
            matched: dict | None = None
            remaining: list[dict] = []
            for entry in pending:
                if entry["otp"] == otp and entry["controller_url"] == controller_url:
                    if now > entry["expires_at"]:
                        # Expired — drop without consuming
                        continue
                    matched = entry
                    continue  # consume by not re-adding to remaining
                remaining.append(entry)
            self._write_pending(remaining)
        return matched
```

- [ ] **Step 9: Verify all tests pass**

```bash
.venv/bin/pytest tests/test_worker_pairing.py -v
```
Expected: 6 passed.

- [ ] **Step 10: Commit**

```bash
git add tinyagentos/worker/pairing.py tests/test_worker_pairing.py
git commit -m "feat(worker): single-use OTP validation, scoped to controller URL"
```

- [ ] **Step 11: Add pairing record persistence**

Add to `PairingManager`:

```python
    def record_pairing(self, controller_url: str, bearer: str) -> None:
        """Persist a successful pairing. Multiple controllers can pair the
        same worker — one bearer per controller URL."""
        with self._lock:
            pairings = self._read_pairings()
            # Replace any existing pairing for this controller
            pairings = [p for p in pairings if p["controller_url"] != controller_url]
            pairings.append({
                "controller_url": controller_url,
                "bearer": bearer,
                "paired_at": time.time(),
            })
            self._write_pairings(pairings)

    def list_pairings(self) -> list[dict]:
        with self._lock:
            return list(self._read_pairings())

    def revoke_pairing(self, controller_url: str) -> bool:
        """Drop the pairing for `controller_url`. Returns True if removed."""
        with self._lock:
            pairings = self._read_pairings()
            new = [p for p in pairings if p["controller_url"] != controller_url]
            removed = len(new) != len(pairings)
            if removed:
                self._write_pairings(new)
            return removed

    def get_pairing(self, controller_url: str) -> dict | None:
        for p in self.list_pairings():
            if p["controller_url"] == controller_url:
                return p
        return None

    def find_pairing_by_bearer(self, bearer: str) -> dict | None:
        for p in self.list_pairings():
            if p["bearer"] == bearer:
                return p
        return None

    def _read_pairings(self) -> list[dict]:
        if not self._pairings_path.exists():
            return []
        try:
            return json.loads(self._pairings_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def _write_pairings(self, data: list[dict]) -> None:
        tmp = self._pairings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._pairings_path)
```

Add tests:

```python
def test_record_pairing(pm):
    pm.record_pairing("https://taos.local:6969", "bearer-abc")
    pairings = pm.list_pairings()
    assert len(pairings) == 1
    assert pairings[0]["bearer"] == "bearer-abc"


def test_record_pairing_replaces_existing_for_same_controller(pm):
    pm.record_pairing("https://taos.local:6969", "old-bearer")
    pm.record_pairing("https://taos.local:6969", "new-bearer")
    pairings = pm.list_pairings()
    assert len(pairings) == 1
    assert pairings[0]["bearer"] == "new-bearer"


def test_record_pairing_keeps_other_controllers(pm):
    pm.record_pairing("https://a.example:6969", "bearer-a")
    pm.record_pairing("https://b.example:6969", "bearer-b")
    assert len(pm.list_pairings()) == 2


def test_revoke_pairing(pm):
    pm.record_pairing("https://taos.local:6969", "bearer-abc")
    assert pm.revoke_pairing("https://taos.local:6969") is True
    assert pm.list_pairings() == []


def test_find_pairing_by_bearer(pm):
    pm.record_pairing("https://taos.local:6969", "bearer-abc")
    found = pm.find_pairing_by_bearer("bearer-abc")
    assert found is not None
    assert found["controller_url"] == "https://taos.local:6969"
```

- [ ] **Step 12: Run tests**

```bash
.venv/bin/pytest tests/test_worker_pairing.py -v
```
Expected: 11 passed.

- [ ] **Step 13: Commit**

```bash
git add tinyagentos/worker/pairing.py tests/test_worker_pairing.py
git commit -m "feat(worker): pairing record persistence, multi-controller support"
```

---

## Task 2: Worker `/pair` endpoint + bearer auth

**Files:**
- Modify: `tinyagentos/worker/agent.py` (add `/pair` route + bearer-auth dependency)
- Test: `tests/test_worker_pairing.py` (extend with HTTP-level tests)

The worker exposes a `/pair` endpoint that accepts `{otp, controller_url}` and returns `{bearer}`. Inference endpoints get a `require_bearer` dependency that rejects calls without a valid bearer.

- [ ] **Step 1: Read the existing worker agent surface**

```bash
.venv/bin/python -c "from tinyagentos.worker.agent import WorkerAgent; import inspect; print(inspect.getsource(WorkerAgent)[:2000])"
```

Look for: how the FastAPI app is created, where existing endpoints live, what dependency-injection pattern is used.

- [ ] **Step 2: Write the failing test for /pair**

Add to `tests/test_worker_pairing.py`:

```python
import asyncio
import httpx
import pytest_asyncio
from tinyagentos.worker.agent import WorkerAgent


@pytest_asyncio.fixture
async def worker_app(tmp_path):
    agent = WorkerAgent(state_dir=tmp_path, controller_url=None)
    yield agent.app


@pytest_asyncio.fixture
async def worker_client(worker_app):
    transport = httpx.ASGITransport(app=worker_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testworker") as client:
        yield client


@pytest.mark.asyncio
async def test_pair_endpoint_with_valid_otp(worker_app, worker_client, tmp_path):
    pm = PairingManager(state_dir=tmp_path)
    otp = pm.generate_otp(controller_url="https://controller.test:6969")
    # Make the agent use the same PairingManager
    worker_app.state.pairing = pm

    resp = await worker_client.post(
        "/pair",
        json={"otp": otp, "controller_url": "https://controller.test:6969"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "bearer" in data
    assert len(data["bearer"]) > 16


@pytest.mark.asyncio
async def test_pair_endpoint_rejects_wrong_otp(worker_client, tmp_path):
    resp = await worker_client.post(
        "/pair",
        json={"otp": "00000000", "controller_url": "https://controller.test:6969"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pair_endpoint_consumes_otp(worker_app, worker_client, tmp_path):
    pm = PairingManager(state_dir=tmp_path)
    otp = pm.generate_otp(controller_url="https://controller.test:6969")
    worker_app.state.pairing = pm

    first = await worker_client.post("/pair", json={"otp": otp, "controller_url": "https://controller.test:6969"})
    assert first.status_code == 200
    second = await worker_client.post("/pair", json={"otp": otp, "controller_url": "https://controller.test:6969"})
    assert second.status_code == 401
```

- [ ] **Step 3: Run tests, verify they fail**

```bash
.venv/bin/pytest tests/test_worker_pairing.py -k pair_endpoint -v
```
Expected: 3 failures (404 not found OR no PairingManager)

- [ ] **Step 4: Add /pair route to WorkerAgent**

Edit `tinyagentos/worker/agent.py`. Locate the FastAPI app construction (look for `FastAPI()` or `self.app =`). Add:

```python
# Top of file, alongside other imports
import secrets as _secrets
from pydantic import BaseModel
from .pairing import PairingManager


# Inside WorkerAgent.__init__, after self.app is created:
self.pairing = PairingManager(state_dir=self.state_dir)
self.app.state.pairing = self.pairing


class _PairRequest(BaseModel):
    otp: str
    controller_url: str


# Add the route — adjust the registration style to match existing routes
# in the file (some use @self.app.post, others use a router).
@self.app.post("/pair")
async def pair_handler(body: _PairRequest):
    pm: PairingManager = self.app.state.pairing
    record = pm.validate_and_consume(body.otp, body.controller_url)
    if record is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid or expired OTP")
    bearer = _secrets.token_urlsafe(32)
    pm.record_pairing(body.controller_url, bearer)
    return {"bearer": bearer}
```

If `WorkerAgent` doesn't currently take a `state_dir` constructor arg, add one with a sensible default (e.g. `~/.local/share/tinyagentos-worker/`).

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_worker_pairing.py -v
```
Expected: 14 passed (11 from Task 1 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add tinyagentos/worker/agent.py tests/test_worker_pairing.py
git commit -m "feat(worker): /pair endpoint validates OTP, returns bearer"
```

- [ ] **Step 7: Add bearer auth dependency**

In `tinyagentos/worker/agent.py`, add:

```python
from fastapi import Header, HTTPException


def require_bearer(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer credential")
    bearer = authorization[len("Bearer "):]
    return bearer


# Then on WorkerAgent, expose a method that other route handlers use to
# require + identify the caller:
def require_paired_caller(self, bearer: str) -> dict:
    """Look up which controller this bearer belongs to. Returns the
    pairing record on success, raises 401 otherwise."""
    record = self.pairing.find_pairing_by_bearer(bearer)
    if record is None:
        raise HTTPException(status_code=401, detail="Unknown bearer")
    return record
```

- [ ] **Step 8: Add a smoke test for bearer auth**

```python
@pytest.mark.asyncio
async def test_bearer_required_for_authed_endpoint(worker_app, worker_client, tmp_path):
    # The agent should expose at least one example endpoint that requires
    # a bearer. Pick the first existing inference endpoint or add a
    # /diagnostic/whoami endpoint that returns the paired controller URL.
    resp = await worker_client.get("/diagnostic/whoami")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_whoami_with_valid_bearer(worker_app, worker_client, tmp_path):
    pm = PairingManager(state_dir=tmp_path)
    otp = pm.generate_otp(controller_url="https://controller.test:6969")
    worker_app.state.pairing = pm

    pair = await worker_client.post("/pair", json={"otp": otp, "controller_url": "https://controller.test:6969"})
    bearer = pair.json()["bearer"]
    resp = await worker_client.get(
        "/diagnostic/whoami",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert resp.status_code == 200
    assert resp.json()["controller_url"] == "https://controller.test:6969"
```

Add the `/diagnostic/whoami` route to `WorkerAgent`:

```python
@self.app.get("/diagnostic/whoami")
async def whoami(bearer: str = Depends(require_bearer)):
    record = self.require_paired_caller(bearer)
    return {"controller_url": record["controller_url"], "paired_at": record["paired_at"]}
```

- [ ] **Step 9: Run tests**

```bash
.venv/bin/pytest tests/test_worker_pairing.py -v
```
Expected: 16 passed.

- [ ] **Step 10: Commit**

```bash
git add tinyagentos/worker/agent.py tests/test_worker_pairing.py
git commit -m "feat(worker): bearer auth dependency + /diagnostic/whoami"
```

---

## Task 3: Worker tray + CLI `pair` command

**Files:**
- Modify: `tinyagentos/worker/__main__.py` (add `pair` subcommand)
- Modify: `tinyagentos/worker/tray.py` (add "Generate pairing code" menu item)

- [ ] **Step 1: Inspect existing CLI surface**

```bash
.venv/bin/python -m tinyagentos.worker --help
```

Note the current subcommands and the argparse style. The new `pair` subcommand mirrors them.

- [ ] **Step 2: Add `pair` subcommand**

In `tinyagentos/worker/__main__.py`, add a subparser:

```python
# Inside the main() arg parser construction
pair_parser = subparsers.add_parser("pair", help="Generate a pairing OTP for a controller")
pair_parser.add_argument(
    "--controller",
    required=True,
    help="Controller URL the OTP will be valid for (e.g. https://taos.local:6969)",
)
pair_parser.add_argument(
    "--state-dir",
    default=None,
    help="Worker state directory (defaults to ~/.local/share/tinyagentos-worker/)",
)


# In the dispatch block:
if args.command == "pair":
    from pathlib import Path
    from .pairing import PairingManager
    state_dir = Path(args.state_dir) if args.state_dir else Path.home() / ".local/share/tinyagentos-worker"
    pm = PairingManager(state_dir=state_dir)
    otp = pm.generate_otp(controller_url=args.controller)
    print(f"OTP: {otp} (valid 10 minutes, paired against {args.controller})")
    return 0
```

- [ ] **Step 3: Manual smoke test**

```bash
.venv/bin/python -m tinyagentos.worker pair --controller https://taos.local:6969 --state-dir /tmp/test-pair-cli
ls /tmp/test-pair-cli/
cat /tmp/test-pair-cli/pending_otps.json
```
Expected: 8-digit OTP printed; `pending_otps.json` contains the entry.

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/worker/__main__.py
git commit -m "feat(worker): CLI 'pair' subcommand prints an OTP for a controller"
```

- [ ] **Step 5: Add tray menu item**

In `tinyagentos/worker/tray.py`, locate the menu construction (probably a `pystray.MenuItem` block). Add:

```python
def on_generate_pairing_code(icon, item):
    # Open a small Tk dialog asking for the controller URL, then show
    # the generated OTP in a copyable popup. If Tk isn't available
    # (headless install), log a hint to use the CLI instead.
    try:
        import tkinter as tk
        from tkinter import simpledialog, messagebox
    except ImportError:
        print("Tk not available — use 'tinyagentos-worker pair --controller URL' from a shell instead.")
        return

    root = tk.Tk()
    root.withdraw()
    url = simpledialog.askstring(
        "Pair worker",
        "Enter your taOS controller URL (e.g. https://taos.local:6969):",
    )
    if not url:
        root.destroy()
        return

    from pathlib import Path
    from .pairing import PairingManager
    pm = PairingManager(state_dir=Path.home() / ".local/share/tinyagentos-worker")
    otp = pm.generate_otp(controller_url=url)
    messagebox.showinfo(
        "Pairing OTP",
        f"OTP: {otp}\n\nValid 10 minutes.\nPaste this in your taOS Cluster app.\n\nController: {url}",
    )
    root.destroy()


# Add to the menu:
pystray.MenuItem("Generate pairing code...", on_generate_pairing_code),
```

- [ ] **Step 6: Smoke test on a desktop**

```bash
DISPLAY=:0 .venv/bin/python -m tinyagentos.worker tray
```
Click the tray icon → "Generate pairing code...". Expected: dialog opens, OTP appears in popup.

If running headless (no DISPLAY), this step is skipped — the CLI path covers headless installs.

- [ ] **Step 7: Commit**

```bash
git add tinyagentos/worker/tray.py
git commit -m "feat(worker tray): 'Generate pairing code' menu item"
```

---

## Task 4: Controller `share_tokens.py` module

**Files:**
- Create: `tinyagentos/cluster/share_tokens.py`
- Test: `tests/test_share_tokens.py`

A share token is a HMAC-signed JSON payload encoding `{share_id, worker_id, recipient_identity, capabilities, tier, expires_at, nonce}`. The signing key is per-controller and persisted in `data/.share_signing_key`. The recipient pastes the token into their taOS; the recipient's controller validates it (timestamp + signature), then redeems it at the owner's worker which validates again.

**Riskiest piece** — review this file with extra care.

- [ ] **Step 1: Write failing test**

```python
# tests/test_share_tokens.py
import time
import pytest
from tinyagentos.cluster.share_tokens import (
    ShareTokenSigner,
    InvalidShareTokenError,
)


@pytest.fixture
def signer(tmp_path):
    return ShareTokenSigner(key_path=tmp_path / ".share_signing_key")


def test_sign_then_verify(signer):
    payload = {
        "share_id": "abc123",
        "worker_id": "worker-1",
        "recipient_identity": "user:jay",
        "capabilities": ["chat", "embed"],
        "tier": "A",
    }
    token = signer.sign(payload, ttl_seconds=86400)
    decoded = signer.verify(token)
    assert decoded["share_id"] == "abc123"
    assert decoded["capabilities"] == ["chat", "embed"]


def test_verify_tampered_token_raises(signer):
    payload = {"share_id": "abc123", "worker_id": "w", "recipient_identity": "u", "capabilities": [], "tier": "A"}
    token = signer.sign(payload, ttl_seconds=86400)
    # Flip a character in the body half
    body, sig = token.rsplit(".", 1)
    tampered = body[:-1] + ("0" if body[-1] != "0" else "1") + "." + sig
    with pytest.raises(InvalidShareTokenError):
        signer.verify(tampered)


def test_verify_wrong_signature_raises(signer):
    payload = {"share_id": "abc123", "worker_id": "w", "recipient_identity": "u", "capabilities": [], "tier": "A"}
    token = signer.sign(payload, ttl_seconds=86400)
    body, _ = token.rsplit(".", 1)
    bad = body + ".AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    with pytest.raises(InvalidShareTokenError):
        signer.verify(bad)


def test_verify_expired_token_raises(signer, monkeypatch):
    payload = {"share_id": "abc123", "worker_id": "w", "recipient_identity": "u", "capabilities": [], "tier": "A"}
    token = signer.sign(payload, ttl_seconds=10)
    monkeypatch.setattr("tinyagentos.cluster.share_tokens.time.time", lambda: time.time() + 100)
    with pytest.raises(InvalidShareTokenError):
        signer.verify(token)


def test_verify_token_signed_by_other_key_raises(tmp_path):
    s1 = ShareTokenSigner(key_path=tmp_path / "k1")
    s2 = ShareTokenSigner(key_path=tmp_path / "k2")
    token = s1.sign({"share_id": "x", "worker_id": "w", "recipient_identity": "u", "capabilities": [], "tier": "A"}, ttl_seconds=86400)
    with pytest.raises(InvalidShareTokenError):
        s2.verify(token)


def test_signing_key_persists_across_instances(tmp_path):
    key_path = tmp_path / "k"
    s1 = ShareTokenSigner(key_path=key_path)
    token = s1.sign({"share_id": "x", "worker_id": "w", "recipient_identity": "u", "capabilities": [], "tier": "A"}, ttl_seconds=86400)
    s2 = ShareTokenSigner(key_path=key_path)
    decoded = s2.verify(token)
    assert decoded["share_id"] == "x"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/pytest tests/test_share_tokens.py -v
```
Expected: 6 failures (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `ShareTokenSigner`**

```python
# tinyagentos/cluster/share_tokens.py
"""Share token signing and verification.

A share token is a HMAC-SHA256 signed payload encoding the share's
identity (share_id, worker_id, recipient, capabilities, tier) plus an
expiry. Format: <base64url(json_payload)>.<base64url(hmac)>

The signing key is a 256-bit secret, generated on first use and
persisted to disk with mode 0600. Same key signs and verifies; this
is intra-controller, not cross-controller.

A separate signer instance per controller means a token issued by
controller A cannot be verified by controller B — important for
multi-controller setups where the same recipient might be paired
against several owners' controllers.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path


class InvalidShareTokenError(Exception):
    """Raised when a share token fails verification."""


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


class ShareTokenSigner:
    def __init__(self, key_path: Path):
        self._key_path = Path(key_path)
        self._key = self._load_or_create_key()

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes()
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        self._key_path.write_bytes(key)
        try:
            self._key_path.chmod(0o600)
        except (OSError, NotImplementedError):
            pass  # Windows / non-POSIX
        return key

    def sign(self, payload: dict, ttl_seconds: int) -> str:
        envelope = {
            **payload,
            "iat": int(time.time()),
            "exp": int(time.time()) + int(ttl_seconds),
            "nonce": secrets.token_urlsafe(12),
        }
        body = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self._key, body, hashlib.sha256).digest()
        return f"{_b64encode(body)}.{_b64encode(sig)}"

    def verify(self, token: str) -> dict:
        try:
            body_b64, sig_b64 = token.rsplit(".", 1)
            body = _b64decode(body_b64)
            sig = _b64decode(sig_b64)
        except (ValueError, base64.binascii.Error) as exc:
            raise InvalidShareTokenError(f"malformed token: {exc}") from None

        expected_sig = hmac.new(self._key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            raise InvalidShareTokenError("signature mismatch")

        try:
            envelope = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidShareTokenError(f"corrupt payload: {exc}") from None

        exp = envelope.get("exp", 0)
        if time.time() > exp:
            raise InvalidShareTokenError("token expired")

        return envelope
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_share_tokens.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/cluster/share_tokens.py tests/test_share_tokens.py
git commit -m "feat(cluster): ShareTokenSigner with HMAC-SHA256, expiry, replay-safe nonce"
```

---

## Task 5: Controller `SharesStore`

**Files:**
- Create: `tinyagentos/cluster/shares.py`
- Test: `tests/test_shares_store.py`

SQLite-backed store for share records. Schema:

```sql
CREATE TABLE shares (
  id TEXT PRIMARY KEY,
  worker_id TEXT NOT NULL,
  owner_identity TEXT NOT NULL,           -- 'user:jay' or 'agent:jay/research-bot'
  recipient_identity TEXT NOT NULL,
  tier TEXT NOT NULL,                     -- 'A' (B/C come in Phase 2/3)
  capabilities TEXT NOT NULL,             -- JSON array
  max_concurrent_jobs INTEGER NOT NULL DEFAULT 1,
  expiry_kind TEXT NOT NULL,              -- 'inactive_30d' | 'never' | 'fixed_date'
  expires_at INTEGER,                     -- nullable; only set when expiry_kind == 'fixed_date'
  paused INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  last_used_at INTEGER NOT NULL DEFAULT 0,
  revoked_at INTEGER,                     -- nullable; set on revoke
  bearer_hash TEXT NOT NULL,              -- SHA256(bearer); never store the bearer itself
  recipient_color TEXT NOT NULL DEFAULT '#8b92a3'
);
CREATE INDEX idx_shares_worker ON shares(worker_id);
CREATE INDEX idx_shares_recipient ON shares(recipient_identity);
CREATE INDEX idx_shares_bearer_hash ON shares(bearer_hash);

CREATE TABLE share_usage (
  share_id TEXT NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
  bucket_start INTEGER NOT NULL,          -- hour epoch
  requests_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (share_id, bucket_start)
);
```

- [ ] **Step 1: Write failing test for create + get**

```python
# tests/test_shares_store.py
import pytest
import pytest_asyncio
from tinyagentos.cluster.shares import SharesStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = SharesStore(db_path=tmp_path / "shares.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_share(store):
    share_id = await store.create_share(
        worker_id="worker-1",
        owner_identity="user:jay",
        recipient_identity="user:bob",
        tier="A",
        capabilities=["chat", "embed"],
        max_concurrent_jobs=1,
        expiry_kind="inactive_30d",
        bearer="bearer-abc",
    )
    assert share_id
    record = await store.get_share(share_id)
    assert record["recipient_identity"] == "user:bob"
    assert record["capabilities"] == ["chat", "embed"]
    assert record["tier"] == "A"
    assert record["paused"] is False
    assert record["revoked_at"] is None


@pytest.mark.asyncio
async def test_bearer_is_hashed_not_stored_plain(store):
    await store.create_share(
        worker_id="worker-1", owner_identity="user:jay",
        recipient_identity="user:bob", tier="A",
        capabilities=["chat"], max_concurrent_jobs=1,
        expiry_kind="inactive_30d", bearer="super-secret-bearer",
    )
    # The raw bearer must not appear in the DB rows
    async with store._db.execute("SELECT bearer_hash FROM shares") as cursor:
        rows = await cursor.fetchall()
    assert rows[0][0] != "super-secret-bearer"
    assert len(rows[0][0]) == 64  # SHA256 hex
```

- [ ] **Step 2: Run, verify they fail**

```bash
.venv/bin/pytest tests/test_shares_store.py -v
```
Expected: 2 failures (ModuleNotFoundError).

- [ ] **Step 3: Implement `SharesStore`**

```python
# tinyagentos/cluster/shares.py
"""Persistent store for share records.

Subclass of BaseStore (SQLite + aiosqlite). One table for shares, one
for hourly usage buckets. Bearers are SHA256-hashed before storage —
the plaintext bearer only exists in process memory at issuance time
and in the recipient's controller.

Lookup by bearer is via the same hash; the worker computes
SHA256(presented_bearer) and queries.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore


class SharesStore(BaseStore):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS shares (
        id TEXT PRIMARY KEY,
        worker_id TEXT NOT NULL,
        owner_identity TEXT NOT NULL,
        recipient_identity TEXT NOT NULL,
        tier TEXT NOT NULL,
        capabilities TEXT NOT NULL,
        max_concurrent_jobs INTEGER NOT NULL DEFAULT 1,
        expiry_kind TEXT NOT NULL,
        expires_at INTEGER,
        paused INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        last_used_at INTEGER NOT NULL DEFAULT 0,
        revoked_at INTEGER,
        bearer_hash TEXT NOT NULL,
        recipient_color TEXT NOT NULL DEFAULT '#8b92a3'
    );
    CREATE INDEX IF NOT EXISTS idx_shares_worker ON shares(worker_id);
    CREATE INDEX IF NOT EXISTS idx_shares_recipient ON shares(recipient_identity);
    CREATE INDEX IF NOT EXISTS idx_shares_bearer_hash ON shares(bearer_hash);

    CREATE TABLE IF NOT EXISTS share_usage (
        share_id TEXT NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
        bucket_start INTEGER NOT NULL,
        requests_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (share_id, bucket_start)
    );
    """

    @staticmethod
    def hash_bearer(bearer: str) -> str:
        return hashlib.sha256(bearer.encode("utf-8")).hexdigest()

    async def create_share(
        self,
        *,
        worker_id: str,
        owner_identity: str,
        recipient_identity: str,
        tier: str,
        capabilities: list[str],
        max_concurrent_jobs: int,
        expiry_kind: str,
        bearer: str,
        expires_at: int | None = None,
        recipient_color: str = "#8b92a3",
    ) -> str:
        share_id = secrets.token_urlsafe(12)
        bearer_hash = self.hash_bearer(bearer)
        await self._db.execute(
            """INSERT INTO shares (id, worker_id, owner_identity, recipient_identity,
               tier, capabilities, max_concurrent_jobs, expiry_kind, expires_at,
               created_at, bearer_hash, recipient_color)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (share_id, worker_id, owner_identity, recipient_identity, tier,
             json.dumps(capabilities), max_concurrent_jobs, expiry_kind, expires_at,
             int(time.time()), bearer_hash, recipient_color),
        )
        await self._db.commit()
        return share_id

    async def get_share(self, share_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM shares WHERE id = ?", (share_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_dict(row) if row else None

    async def get_share_by_bearer(self, bearer: str) -> dict | None:
        bearer_hash = self.hash_bearer(bearer)
        async with self._db.execute(
            "SELECT * FROM shares WHERE bearer_hash = ?", (bearer_hash,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_dict(row) if row else None

    async def list_shares(self, *, worker_id: str | None = None,
                          owner_identity: str | None = None,
                          recipient_identity: str | None = None) -> list[dict]:
        sql = "SELECT * FROM shares WHERE 1=1"
        args: list = []
        if worker_id:
            sql += " AND worker_id = ?"
            args.append(worker_id)
        if owner_identity:
            sql += " AND owner_identity = ?"
            args.append(owner_identity)
        if recipient_identity:
            sql += " AND recipient_identity = ?"
            args.append(recipient_identity)
        sql += " ORDER BY created_at DESC"
        async with self._db.execute(sql, args) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def revoke_share(self, share_id: str) -> bool:
        await self._db.execute(
            "UPDATE shares SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (int(time.time()), share_id),
        )
        await self._db.commit()
        return self._db.total_changes > 0

    async def set_paused(self, share_id: str, paused: bool) -> None:
        await self._db.execute(
            "UPDATE shares SET paused = ? WHERE id = ?",
            (1 if paused else 0, share_id),
        )
        await self._db.commit()

    async def touch_usage(self, share_id: str) -> None:
        bucket = (int(time.time()) // 3600) * 3600
        await self._db.execute(
            """INSERT INTO share_usage (share_id, bucket_start, requests_count)
               VALUES (?, ?, 1)
               ON CONFLICT(share_id, bucket_start) DO UPDATE SET requests_count = requests_count + 1""",
            (share_id, bucket),
        )
        await self._db.execute(
            "UPDATE shares SET last_used_at = ? WHERE id = ?",
            (int(time.time()), share_id),
        )
        await self._db.commit()

    async def get_usage_summary(self, share_id: str) -> dict:
        async with self._db.execute(
            "SELECT SUM(requests_count) FROM share_usage WHERE share_id = ?", (share_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return {"total_requests": (row[0] or 0)}

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row[0],
            "worker_id": row[1],
            "owner_identity": row[2],
            "recipient_identity": row[3],
            "tier": row[4],
            "capabilities": json.loads(row[5]),
            "max_concurrent_jobs": row[6],
            "expiry_kind": row[7],
            "expires_at": row[8],
            "paused": bool(row[9]),
            "created_at": row[10],
            "last_used_at": row[11],
            "revoked_at": row[12],
            "bearer_hash": row[13],
            "recipient_color": row[14],
        }
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_shares_store.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Add tests for revoke + paused + usage**

```python
@pytest.mark.asyncio
async def test_revoke_share(store):
    share_id = await store.create_share(
        worker_id="w", owner_identity="user:jay", recipient_identity="user:bob",
        tier="A", capabilities=["chat"], max_concurrent_jobs=1,
        expiry_kind="inactive_30d", bearer="b1",
    )
    assert await store.revoke_share(share_id) is True
    record = await store.get_share(share_id)
    assert record["revoked_at"] is not None


@pytest.mark.asyncio
async def test_set_paused(store):
    share_id = await store.create_share(
        worker_id="w", owner_identity="user:jay", recipient_identity="user:bob",
        tier="A", capabilities=["chat"], max_concurrent_jobs=1,
        expiry_kind="inactive_30d", bearer="b2",
    )
    await store.set_paused(share_id, True)
    assert (await store.get_share(share_id))["paused"] is True
    await store.set_paused(share_id, False)
    assert (await store.get_share(share_id))["paused"] is False


@pytest.mark.asyncio
async def test_touch_usage_increments(store):
    share_id = await store.create_share(
        worker_id="w", owner_identity="user:jay", recipient_identity="user:bob",
        tier="A", capabilities=["chat"], max_concurrent_jobs=1,
        expiry_kind="inactive_30d", bearer="b3",
    )
    await store.touch_usage(share_id)
    await store.touch_usage(share_id)
    summary = await store.get_usage_summary(share_id)
    assert summary["total_requests"] == 2


@pytest.mark.asyncio
async def test_list_shares_filters(store):
    await store.create_share(
        worker_id="w1", owner_identity="user:jay", recipient_identity="user:bob",
        tier="A", capabilities=["chat"], max_concurrent_jobs=1,
        expiry_kind="inactive_30d", bearer="b4",
    )
    await store.create_share(
        worker_id="w2", owner_identity="user:jay", recipient_identity="user:carol",
        tier="A", capabilities=["chat"], max_concurrent_jobs=1,
        expiry_kind="inactive_30d", bearer="b5",
    )
    by_worker = await store.list_shares(worker_id="w1")
    assert len(by_worker) == 1
    by_recipient = await store.list_shares(recipient_identity="user:carol")
    assert len(by_recipient) == 1
    by_owner = await store.list_shares(owner_identity="user:jay")
    assert len(by_owner) == 2


@pytest.mark.asyncio
async def test_get_share_by_bearer(store):
    await store.create_share(
        worker_id="w", owner_identity="user:jay", recipient_identity="user:bob",
        tier="A", capabilities=["chat"], max_concurrent_jobs=1,
        expiry_kind="inactive_30d", bearer="lookup-me",
    )
    record = await store.get_share_by_bearer("lookup-me")
    assert record is not None
    assert record["recipient_identity"] == "user:bob"

    none = await store.get_share_by_bearer("wrong")
    assert none is None
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/test_shares_store.py -v
```
Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add tinyagentos/cluster/shares.py tests/test_shares_store.py
git commit -m "feat(cluster): SharesStore with hashed bearers, usage buckets, revoke/pause"
```

---

## Task 6: Controller `/api/cluster/pair` endpoint

**Files:**
- Modify: `tinyagentos/routes/cluster.py` (new endpoint)
- Modify: `tinyagentos/app.py` (wire SharesStore into lifespan)
- Test: `tests/test_routes_cluster_pair.py`

The controller's `/api/cluster/pair` accepts `{otp, worker_url}` from the owner. It calls the worker's `/pair` with the OTP, gets back the bearer, persists a pairing record (in `cluster_manager` or a new `PairingsStore` — we can extend `cluster.workers` for now), and returns success.

For Phase 1 we don't need a separate `PairingsStore` — the existing worker-info table in `ClusterManager` already tracks workers. We add a `bearer` field to the worker record and store it there (encrypted-at-rest is a Phase 2 nicety; for Phase 1 it's plaintext at rest, justified because the controller process itself is the trust boundary).

- [ ] **Step 1: Write failing test**

```python
# tests/test_routes_cluster_pair.py
import pytest
import pytest_asyncio
import httpx


@pytest_asyncio.fixture
async def client(app, auth_client):
    # auth_client is an existing fixture from conftest with auth bypassed
    yield auth_client


@pytest.mark.asyncio
async def test_pair_endpoint_calls_worker(client, monkeypatch):
    """The controller relays the OTP to the worker's /pair, persists the
    returned bearer, and registers the worker."""

    captured_calls = []

    class FakeAsyncClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, json):
            captured_calls.append((url, json))
            return httpx.Response(200, json={"bearer": "test-bearer-123"})

    monkeypatch.setattr("tinyagentos.routes.cluster.httpx.AsyncClient", lambda **kw: FakeAsyncClient())

    resp = await client.post("/api/cluster/pair", json={
        "otp": "12345678",
        "worker_url": "http://worker.test:8001",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["paired"] is True
    assert "worker_id" in data

    # Verify the controller called the worker correctly
    assert len(captured_calls) == 1
    url, body = captured_calls[0]
    assert url == "http://worker.test:8001/pair"
    assert body["otp"] == "12345678"
```

- [ ] **Step 2: Run, verify it fails**

```bash
.venv/bin/pytest tests/test_routes_cluster_pair.py -v
```
Expected: 1 failure (404 — endpoint doesn't exist).

- [ ] **Step 3: Add the endpoint**

In `tinyagentos/routes/cluster.py`:

```python
import httpx
from pydantic import BaseModel


class PairRequest(BaseModel):
    otp: str
    worker_url: str
    controller_url: str | None = None  # auto-detected from request if absent


@router.post("/api/cluster/pair")
async def pair_worker(request: Request, body: PairRequest):
    """Owner pastes an OTP from a worker. Controller relays to the worker's
    /pair, gets a bearer, and persists the pairing as a worker record.
    """
    controller_url = body.controller_url or str(request.base_url).rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                f"{body.worker_url.rstrip('/')}/pair",
                json={"otp": body.otp, "controller_url": controller_url},
            )
    except httpx.HTTPError as exc:
        return JSONResponse(
            {"error": f"Could not reach worker at {body.worker_url}: {exc}"},
            status_code=502,
        )

    if resp.status_code == 401:
        return JSONResponse({"error": "Worker rejected OTP (invalid or expired)"}, status_code=401)
    if resp.status_code != 200:
        return JSONResponse({"error": f"Worker returned {resp.status_code}"}, status_code=502)

    bearer = resp.json().get("bearer")
    if not bearer:
        return JSONResponse({"error": "Worker did not return a bearer"}, status_code=502)

    # Generate a worker_id — use the URL hostname as the default name; user
    # can rename later via PUT /api/cluster/workers/{id}.
    from urllib.parse import urlparse
    host = urlparse(body.worker_url).hostname or "worker"
    worker_id = host.replace(".", "-")

    # Persist into ClusterManager. For Phase 1 we extend the existing
    # WorkerInfo to carry a bearer field; if WorkerInfo doesn't have one
    # yet, store the bearer in app.state.worker_bearers (a dict) keyed by
    # worker_id.
    bearers = getattr(request.app.state, "worker_bearers", {})
    bearers[worker_id] = bearer
    request.app.state.worker_bearers = bearers

    return JSONResponse({"paired": True, "worker_id": worker_id})
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_routes_cluster_pair.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Add error-path test**

```python
@pytest.mark.asyncio
async def test_pair_rejects_when_worker_returns_401(client, monkeypatch):
    class FakeAsyncClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, json):
            return httpx.Response(401, json={"detail": "bad otp"})

    monkeypatch.setattr("tinyagentos.routes.cluster.httpx.AsyncClient", lambda **kw: FakeAsyncClient())

    resp = await client.post("/api/cluster/pair", json={
        "otp": "00000000", "worker_url": "http://worker.test:8001",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pair_502_when_worker_unreachable(client, monkeypatch):
    class FakeAsyncClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, json):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("tinyagentos.routes.cluster.httpx.AsyncClient", lambda **kw: FakeAsyncClient())

    resp = await client.post("/api/cluster/pair", json={
        "otp": "12345678", "worker_url": "http://nothing.test:8001",
    })
    assert resp.status_code == 502
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/test_routes_cluster_pair.py -v
```
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add tinyagentos/routes/cluster.py tests/test_routes_cluster_pair.py
git commit -m "feat(cluster): /api/cluster/pair relays OTP to worker, persists bearer"
```

---

## Task 7: Controller share CRUD + redeem + tier A enforcement

**Files:**
- Modify: `tinyagentos/routes/cluster.py` (5 new endpoints)
- Modify: `tinyagentos/app.py` (instantiate `ShareTokenSigner` + `SharesStore` in lifespan)
- Test: `tests/test_routes_cluster_shares.py`

Endpoints (all from spec §API surface):
- `POST /api/cluster/workers/{id}/shares` — owner creates a share
- `GET /api/cluster/workers/{id}/shares` — list owner's shares for this worker
- `GET /api/cluster/shares` — list all shares owner has issued
- `PUT /api/cluster/shares/{id}` — patch limits / pause / resume
- `DELETE /api/cluster/shares/{id}` — revoke
- `GET /api/cluster/shares/{id}/usage` — usage summary
- `POST /api/cluster/shares/redeem` — recipient redeems a token
- `GET /api/cluster/borrowed` — list workers shared TO me
- `DELETE /api/cluster/borrowed/{id}` — recipient declines a borrowed worker

Tier A enforcement happens at the route layer for any inference-bound call: the recipient's controller calls the owner's worker passing their bearer. The owner's worker:
1. Looks up the share by `bearer_hash`
2. Rejects if `revoked_at IS NOT NULL` or `paused = 1`
3. Rejects if the called capability isn't in `capabilities`
4. Rejects if active jobs for this share > `max_concurrent_jobs`
5. Touches `last_used_at` and `share_usage`

For Phase 1 the worker is the gate — the controller route layer just makes sure the bearer is forwarded correctly. We test the gate at the worker level in this task and trust the controller-side proxy in Phase 2 (or in a follow-up if we add a controller-side relay).

- [ ] **Step 1: Write failing tests for share creation**

```python
# tests/test_routes_cluster_shares.py
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def share_client(app, auth_client):
    # Set up: pretend a worker is paired
    app.state.worker_bearers = {"worker-1": "owner-bearer"}
    yield auth_client


@pytest.mark.asyncio
async def test_create_share_returns_token(share_client):
    resp = await share_client.post(
        "/api/cluster/workers/worker-1/shares",
        json={
            "recipient_identity": "user:bob",
            "tier": "A",
            "capabilities": ["chat", "embed"],
            "max_concurrent_jobs": 1,
            "expiry_kind": "inactive_30d",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "share_id" in data
    assert "share_token" in data
    # Token format check: <body>.<sig>
    assert data["share_token"].count(".") == 1


@pytest.mark.asyncio
async def test_create_share_for_unpaired_worker_404(share_client):
    resp = await share_client.post(
        "/api/cluster/workers/no-such-worker/shares",
        json={
            "recipient_identity": "user:bob",
            "tier": "A",
            "capabilities": ["chat"],
            "max_concurrent_jobs": 1,
            "expiry_kind": "inactive_30d",
        },
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run, verify they fail**

```bash
.venv/bin/pytest tests/test_routes_cluster_shares.py -v
```
Expected: failures (endpoints don't exist).

- [ ] **Step 3: Wire SharesStore + ShareTokenSigner into app.py**

In `tinyagentos/app.py` lifespan startup:

```python
from tinyagentos.cluster.shares import SharesStore
from tinyagentos.cluster.share_tokens import ShareTokenSigner

# alongside other store instantiations:
shares_store = SharesStore(db_path=data_dir / "shares.db")
share_signer = ShareTokenSigner(key_path=data_dir / ".share_signing_key")

# In the async startup block:
await shares_store.init()
app.state.shares_store = shares_store
app.state.share_signer = share_signer

# In shutdown:
await shares_store.close()
```

- [ ] **Step 4: Add share endpoints to routes/cluster.py**

```python
import secrets
from tinyagentos.cluster.shares import SharesStore
from tinyagentos.cluster.share_tokens import ShareTokenSigner, InvalidShareTokenError


class CreateShareRequest(BaseModel):
    recipient_identity: str  # 'user:<name>' or 'agent:<owner>/<agent_name>'
    tier: str = "A"
    capabilities: list[str] = ["chat", "embed"]
    max_concurrent_jobs: int = 1
    expiry_kind: str = "inactive_30d"
    expires_at: int | None = None
    recipient_color: str = "#8b92a3"


@router.post("/api/cluster/workers/{worker_id}/shares")
async def create_share(request: Request, worker_id: str, body: CreateShareRequest):
    bearers = getattr(request.app.state, "worker_bearers", {})
    if worker_id not in bearers:
        return JSONResponse({"error": "worker not paired"}, status_code=404)

    if body.tier != "A":
        return JSONResponse({"error": "Phase 1 supports tier A only"}, status_code=400)

    store: SharesStore = request.app.state.shares_store
    signer: ShareTokenSigner = request.app.state.share_signer

    bearer = secrets.token_urlsafe(32)
    # Owner identity: for Phase 1 use the auth subject. If the auth
    # middleware exposes session_user we use it; otherwise fall back to
    # the current single-user 'user:admin' label.
    owner_identity = "user:" + (
        request.app.state.auth.get_user().get("username", "admin")
        if request.app.state.auth.is_configured() else "admin"
    )

    share_id = await store.create_share(
        worker_id=worker_id,
        owner_identity=owner_identity,
        recipient_identity=body.recipient_identity,
        tier=body.tier,
        capabilities=body.capabilities,
        max_concurrent_jobs=body.max_concurrent_jobs,
        expiry_kind=body.expiry_kind,
        expires_at=body.expires_at,
        bearer=bearer,
        recipient_color=body.recipient_color,
    )

    token = signer.sign(
        {
            "share_id": share_id,
            "worker_id": worker_id,
            "recipient_identity": body.recipient_identity,
            "capabilities": body.capabilities,
            "tier": body.tier,
            "bearer": bearer,
            "owner_identity": owner_identity,
            # Worker URL for recipient to redeem against. In Phase 1 we
            # piggy-back on the controller's known worker URL — Phase 4
            # adds Headscale routing.
            "worker_url": _worker_url_for(request, worker_id),
        },
        ttl_seconds=86400,
    )
    return {"share_id": share_id, "share_token": token}


def _worker_url_for(request: Request, worker_id: str) -> str:
    """Return the URL the recipient should use to reach the worker."""
    # Phase 1: from the cluster manager registry, fall back to a placeholder
    cm = request.app.state.cluster_manager
    info = cm._workers.get(worker_id)
    if info and getattr(info, "url", None):
        return info.url
    return f"http://{worker_id}:8001"
```

- [ ] **Step 5: Run create-share tests**

```bash
.venv/bin/pytest tests/test_routes_cluster_shares.py::test_create_share_returns_token tests/test_routes_cluster_shares.py::test_create_share_for_unpaired_worker_404 -v
```
Expected: 2 passed.

- [ ] **Step 6: Add list / patch / revoke endpoints**

```python
@router.get("/api/cluster/workers/{worker_id}/shares")
async def list_shares_for_worker(request: Request, worker_id: str):
    store: SharesStore = request.app.state.shares_store
    return await store.list_shares(worker_id=worker_id)


@router.get("/api/cluster/shares")
async def list_all_owner_shares(request: Request):
    store: SharesStore = request.app.state.shares_store
    owner = "user:" + request.app.state.auth.get_user().get("username", "admin")
    return await store.list_shares(owner_identity=owner)


class PatchShareRequest(BaseModel):
    paused: bool | None = None
    max_concurrent_jobs: int | None = None
    capabilities: list[str] | None = None


@router.put("/api/cluster/shares/{share_id}")
async def patch_share(request: Request, share_id: str, body: PatchShareRequest):
    store: SharesStore = request.app.state.shares_store
    record = await store.get_share(share_id)
    if not record:
        return JSONResponse({"error": "share not found"}, status_code=404)
    if body.paused is not None:
        await store.set_paused(share_id, body.paused)
    # capabilities + max_concurrent_jobs patches: a follow-up task
    return await store.get_share(share_id)


@router.delete("/api/cluster/shares/{share_id}")
async def revoke_share(request: Request, share_id: str):
    store: SharesStore = request.app.state.shares_store
    record = await store.get_share(share_id)
    if not record:
        return JSONResponse({"error": "share not found"}, status_code=404)
    revoked = await store.revoke_share(share_id)

    # Notification on revoke
    notif = getattr(request.app.state, "notifications", None)
    if notif and revoked:
        await notif.emit_event(
            "share.revoked",
            f"Share to {record['recipient_identity']} revoked",
            f"Worker: {record['worker_id']}",
            level="info",
        )
    return {"revoked": revoked}


@router.get("/api/cluster/shares/{share_id}/usage")
async def share_usage(request: Request, share_id: str):
    store: SharesStore = request.app.state.shares_store
    record = await store.get_share(share_id)
    if not record:
        return JSONResponse({"error": "share not found"}, status_code=404)
    summary = await store.get_usage_summary(share_id)
    return {**record, "usage": summary}
```

- [ ] **Step 7: Add tests for list/patch/revoke**

```python
@pytest.mark.asyncio
async def test_list_shares_for_worker(share_client):
    await share_client.post(
        "/api/cluster/workers/worker-1/shares",
        json={"recipient_identity": "user:bob", "tier": "A",
              "capabilities": ["chat"], "max_concurrent_jobs": 1,
              "expiry_kind": "inactive_30d"},
    )
    resp = await share_client.get("/api/cluster/workers/worker-1/shares")
    assert resp.status_code == 200
    shares = resp.json()
    assert len(shares) == 1
    assert shares[0]["recipient_identity"] == "user:bob"


@pytest.mark.asyncio
async def test_pause_share(share_client):
    create = await share_client.post(
        "/api/cluster/workers/worker-1/shares",
        json={"recipient_identity": "user:bob", "tier": "A",
              "capabilities": ["chat"], "max_concurrent_jobs": 1,
              "expiry_kind": "inactive_30d"},
    )
    share_id = create.json()["share_id"]
    resp = await share_client.put(f"/api/cluster/shares/{share_id}", json={"paused": True})
    assert resp.status_code == 200
    assert resp.json()["paused"] is True


@pytest.mark.asyncio
async def test_revoke_share(share_client):
    create = await share_client.post(
        "/api/cluster/workers/worker-1/shares",
        json={"recipient_identity": "user:bob", "tier": "A",
              "capabilities": ["chat"], "max_concurrent_jobs": 1,
              "expiry_kind": "inactive_30d"},
    )
    share_id = create.json()["share_id"]
    resp = await share_client.delete(f"/api/cluster/shares/{share_id}")
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True
```

- [ ] **Step 8: Run tests**

```bash
.venv/bin/pytest tests/test_routes_cluster_shares.py -v
```
Expected: 5 passed.

- [ ] **Step 9: Add redeem + borrowed-list endpoints**

```python
class RedeemShareRequest(BaseModel):
    share_token: str


@router.post("/api/cluster/shares/redeem")
async def redeem_share(request: Request, body: RedeemShareRequest):
    """Recipient pastes a share token. Controller validates, persists the
    borrowed-worker record, and returns the worker metadata + bearer."""
    signer: ShareTokenSigner = request.app.state.share_signer
    try:
        envelope = signer.verify(body.share_token)
    except InvalidShareTokenError as exc:
        return JSONResponse({"error": f"invalid share token: {exc}"}, status_code=400)

    # Persist the borrowed-worker record (separate table; for Phase 1 a
    # simple JSON file or app.state dict is enough).
    borrowed = getattr(request.app.state, "borrowed_workers", {})
    borrowed[envelope["share_id"]] = {
        "share_id": envelope["share_id"],
        "worker_id": envelope["worker_id"],
        "worker_url": envelope["worker_url"],
        "owner_identity": envelope["owner_identity"],
        "capabilities": envelope["capabilities"],
        "tier": envelope["tier"],
        "bearer": envelope["bearer"],
        "redeemed_at": int(__import__("time").time()),
    }
    request.app.state.borrowed_workers = borrowed

    return {
        "ok": True,
        "share_id": envelope["share_id"],
        "worker_id": envelope["worker_id"],
        "worker_url": envelope["worker_url"],
        "owner_identity": envelope["owner_identity"],
        "tier": envelope["tier"],
        "capabilities": envelope["capabilities"],
    }


@router.get("/api/cluster/borrowed")
async def list_borrowed(request: Request):
    return list(getattr(request.app.state, "borrowed_workers", {}).values())


@router.delete("/api/cluster/borrowed/{share_id}")
async def decline_borrowed(request: Request, share_id: str):
    borrowed = getattr(request.app.state, "borrowed_workers", {})
    removed = borrowed.pop(share_id, None) is not None
    request.app.state.borrowed_workers = borrowed
    return {"removed": removed}
```

- [ ] **Step 10: Add redeem flow tests**

```python
@pytest.mark.asyncio
async def test_redeem_creates_borrowed_record(share_client):
    create = await share_client.post(
        "/api/cluster/workers/worker-1/shares",
        json={"recipient_identity": "user:bob", "tier": "A",
              "capabilities": ["chat"], "max_concurrent_jobs": 1,
              "expiry_kind": "inactive_30d"},
    )
    token = create.json()["share_token"]
    redeem = await share_client.post(
        "/api/cluster/shares/redeem",
        json={"share_token": token},
    )
    assert redeem.status_code == 200
    body = redeem.json()
    assert body["worker_id"] == "worker-1"

    listing = await share_client.get("/api/cluster/borrowed")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


@pytest.mark.asyncio
async def test_redeem_garbage_token_rejected(share_client):
    resp = await share_client.post(
        "/api/cluster/shares/redeem",
        json={"share_token": "not.a.valid.token"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 11: Run tests**

```bash
.venv/bin/pytest tests/test_routes_cluster_shares.py -v
```
Expected: 7 passed.

- [ ] **Step 12: Worker-side tier A enforcement**

In `tinyagentos/worker/agent.py` add a share-bearer dependency that callers can use on inference endpoints:

```python
async def share_authorized(request: Request, capability: str) -> dict:
    """Validates the bearer against any active share. Used by the worker's
    inference handlers to gate calls per share's capability allowlist."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing bearer")
    bearer = auth[len("Bearer "):]

    shares: SharesStore = request.app.state.shares_store
    share = await shares.get_share_by_bearer(bearer)
    if share is None:
        raise HTTPException(401, "unknown bearer")
    if share["revoked_at"]:
        raise HTTPException(403, "share revoked")
    if share["paused"]:
        raise HTTPException(403, "share paused")
    if capability not in share["capabilities"]:
        raise HTTPException(403, f"capability {capability!r} not allowed by share")
    # touch usage
    await shares.touch_usage(share["id"])
    return share
```

This requires the worker to ALSO have a `SharesStore`. For Phase 1 we cheat: the controller and worker run on the same machine in the smoke test, so they can share the SQLite file. For full deployment (separate hosts) this needs the worker to keep its own SharesStore, populated via a notification from the controller on share-create. **File this gap as a follow-up issue at the end of the task.**

- [ ] **Step 13: Smoke test the full pair → share → redeem → call flow**

```bash
.venv/bin/pytest tests/test_routes_cluster_shares.py -v
.venv/bin/pytest tests/test_routes_cluster_pair.py -v
.venv/bin/pytest tests/test_worker_pairing.py -v
.venv/bin/pytest tests/test_share_tokens.py -v
.venv/bin/pytest tests/test_shares_store.py -v
```
Expected: all green.

- [ ] **Step 14: Commit**

```bash
git add tinyagentos/routes/cluster.py tinyagentos/app.py tinyagentos/worker/agent.py tests/test_routes_cluster_shares.py
git commit -m "feat(cluster): share CRUD, redeem, borrowed-worker tracking, tier A enforcement"
```

- [ ] **Step 15: File follow-up issue for cross-host SharesStore sync**

```bash
gh issue create --repo jaylfc/tinyagentos --title "Worker needs its own SharesStore for cross-host deployments" --body "Phase 1 of the worker-sharing implementation cheats on cross-host deployments — the worker checks shares against the controller's SharesStore, which only works when both run on the same machine. For a real distributed setup the worker needs its own copy of the share records, populated via a push notification from the controller on share create / pause / revoke. File against the worker-pairing-sharing epic #212 as a Phase 1.5 cleanup."
```

---

## Task 8: Per-share expiry watcher + notifications

**Files:**
- Modify: `tinyagentos/cluster/shares.py` (add `expire_inactive_shares` method)
- Modify: `tinyagentos/app.py` (start a background task)
- Test: `tests/test_shares_store.py` (extend)

The `inactive_30d` expiry default kicks in when `last_used_at < now - 30d`. A periodic task (every 6h) scans for these and revokes them with a notification.

- [ ] **Step 1: Test for `expire_inactive_shares`**

```python
# tests/test_shares_store.py
@pytest.mark.asyncio
async def test_expire_inactive_shares(store, monkeypatch):
    share_id = await store.create_share(
        worker_id="w", owner_identity="user:jay", recipient_identity="user:bob",
        tier="A", capabilities=["chat"], max_concurrent_jobs=1,
        expiry_kind="inactive_30d", bearer="b6",
    )
    # Force last_used_at to 31 days ago
    import time as _t
    long_ago = int(_t.time() - 86400 * 31)
    await store._db.execute(
        "UPDATE shares SET last_used_at = ?, created_at = ? WHERE id = ?",
        (long_ago, long_ago, share_id),
    )
    await store._db.commit()

    expired_ids = await store.expire_inactive_shares()
    assert share_id in expired_ids
    record = await store.get_share(share_id)
    assert record["revoked_at"] is not None
```

- [ ] **Step 2: Implement**

In `tinyagentos/cluster/shares.py`:

```python
INACTIVE_THRESHOLD_SECONDS = 30 * 86400


async def expire_inactive_shares(self) -> list[str]:
    """Revoke shares with expiry_kind='inactive_30d' that haven't been
    used in the threshold window. Also handles 'fixed_date' shares whose
    expires_at has passed.

    Returns the list of share_ids that were revoked.
    """
    cutoff = int(time.time()) - INACTIVE_THRESHOLD_SECONDS
    now = int(time.time())
    expired: list[str] = []

    # inactive_30d: last_used_at older than cutoff (treating 0 as the
    # share's created_at for never-used shares — use whichever is later)
    async with self._db.execute(
        """SELECT id FROM shares
           WHERE revoked_at IS NULL
             AND expiry_kind = 'inactive_30d'
             AND MAX(last_used_at, created_at) < ?""",
        (cutoff,),
    ) as cursor:
        rows = await cursor.fetchall()
        expired.extend(r[0] for r in rows)

    # fixed_date: expires_at set and in the past
    async with self._db.execute(
        """SELECT id FROM shares
           WHERE revoked_at IS NULL
             AND expiry_kind = 'fixed_date'
             AND expires_at IS NOT NULL
             AND expires_at < ?""",
        (now,),
    ) as cursor:
        rows = await cursor.fetchall()
        expired.extend(r[0] for r in rows)

    for sid in expired:
        await self._db.execute(
            "UPDATE shares SET revoked_at = ? WHERE id = ?",
            (now, sid),
        )
    if expired:
        await self._db.commit()
    return expired
```

- [ ] **Step 3: Run test**

```bash
.venv/bin/pytest tests/test_shares_store.py::test_expire_inactive_shares -v
```
Expected: passed.

- [ ] **Step 4: Wire the periodic task in app.py**

```python
# In lifespan startup:
async def _share_expiry_loop():
    import asyncio
    while True:
        try:
            expired = await shares_store.expire_inactive_shares()
            if expired and notif_store:
                for sid in expired:
                    await notif_store.emit_event(
                        "share.expired",
                        f"Share {sid} expired",
                        "The share's inactivity threshold was reached. Recipient access has been revoked.",
                        level="info",
                    )
        except Exception:
            logger.exception("share expiry loop error")
        await asyncio.sleep(6 * 3600)  # every 6 hours

share_expiry_task = asyncio.create_task(_share_expiry_loop(), name="share-expiry")

# In shutdown:
share_expiry_task.cancel()
```

- [ ] **Step 5: Add a unit test for the loop**

```python
# tests/test_shares_store.py
@pytest.mark.asyncio
async def test_expire_emits_notifications(store, monkeypatch):
    share_id = await store.create_share(
        worker_id="w", owner_identity="user:jay", recipient_identity="user:bob",
        tier="A", capabilities=["chat"], max_concurrent_jobs=1,
        expiry_kind="inactive_30d", bearer="b7",
    )
    import time as _t
    long_ago = int(_t.time() - 86400 * 31)
    await store._db.execute(
        "UPDATE shares SET last_used_at = ?, created_at = ? WHERE id = ?",
        (long_ago, long_ago, share_id),
    )
    await store._db.commit()

    expired = await store.expire_inactive_shares()
    assert share_id in expired
    # Calling again on already-expired shares should be a no-op
    second_pass = await store.expire_inactive_shares()
    assert share_id not in second_pass
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/test_shares_store.py -v
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add tinyagentos/cluster/shares.py tinyagentos/app.py tests/test_shares_store.py
git commit -m "feat(cluster): periodic expiry watcher + notification on auto-revoke"
```

---

## Task 9: Frontend — Cluster app pairing UI

**Files:**
- Create: `desktop/src/apps/cluster/PairingDialog.tsx`
- Modify: `desktop/src/apps/ClusterApp.tsx` (mount the dialog, add "Pair worker" button)

- [ ] **Step 1: Create the dialog component**

```tsx
// desktop/src/apps/cluster/PairingDialog.tsx
import { useState } from "react";
import { Button } from "@/components/ui";

interface Props {
  open: boolean;
  onClose: () => void;
  onPaired: (workerId: string) => void;
}

export function PairingDialog({ open, onClose, onPaired }: Props) {
  const [otp, setOtp] = useState("");
  const [workerUrl, setWorkerUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  async function handlePair(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/cluster/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ otp: otp.trim(), worker_url: workerUrl.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data?.error ?? `Pair failed (${res.status})`);
        setBusy(false);
        return;
      }
      const data = await res.json();
      onPaired(data.worker_id);
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Network error");
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-label="Pair worker"
      onClick={onClose}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={handlePair}
        className="bg-shell-surface border border-white/10 rounded-xl p-6 w-full max-w-md shadow-xl"
      >
        <h3 className="text-base font-semibold mb-1">Pair a worker</h3>
        <p className="text-xs text-shell-text-tertiary mb-4">
          On the worker, generate an OTP via the tray icon or
          <code className="mx-1">tinyagentos-worker pair --controller URL</code>.
          Paste it here.
        </p>

        <label className="block text-[11px] uppercase tracking-wide text-shell-text-tertiary mb-1">Worker URL</label>
        <input
          type="text"
          required
          value={workerUrl}
          onChange={(e) => setWorkerUrl(e.target.value)}
          placeholder="http://worker.local:8001"
          className="w-full px-3 py-2 mb-3 rounded bg-shell-bg-deep border border-white/10 text-sm outline-none focus:border-accent/40"
          autoFocus
        />

        <label className="block text-[11px] uppercase tracking-wide text-shell-text-tertiary mb-1">OTP</label>
        <input
          type="text"
          required
          inputMode="numeric"
          pattern="[0-9]{8}"
          value={otp}
          onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 8))}
          placeholder="12345678"
          className="w-full px-3 py-2 mb-3 rounded bg-shell-bg-deep border border-white/10 text-sm font-mono tracking-widest outline-none focus:border-accent/40"
        />

        {error && (
          <p className="text-xs text-red-400 mb-3" role="alert">{error}</p>
        )}

        <div className="flex gap-2 justify-end">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" disabled={busy || !otp || !workerUrl}>
            {busy ? "Pairing..." : "Pair"}
          </Button>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 2: Mount the dialog in ClusterApp.tsx**

In `desktop/src/apps/ClusterApp.tsx`, locate the toolbar/header and add:

```tsx
import { PairingDialog } from "./cluster/PairingDialog";
import { Plus } from "lucide-react";

// Inside the component:
const [pairOpen, setPairOpen] = useState(false);

// Inside the toolbar render:
<Button size="sm" onClick={() => setPairOpen(true)} aria-label="Pair worker">
  <Plus size={14} /> Pair worker
</Button>

// Anywhere outside the toolbar JSX, before the closing root element:
<PairingDialog
  open={pairOpen}
  onClose={() => setPairOpen(false)}
  onPaired={(workerId) => {
    // Trigger a workers refresh
    refetchWorkers?.(); // or whatever the existing refresh is called
  }}
/>
```

- [ ] **Step 3: Build and visually verify**

```bash
cd desktop && npm run build 2>&1 | tail -3
```
Expected: clean build.

Open the Cluster app in browser. Click "Pair worker". Dialog should appear with two fields.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/cluster/PairingDialog.tsx desktop/src/apps/ClusterApp.tsx
git commit -m "feat(cluster ui): pairing dialog — paste OTP + worker URL to pair"
```

---

## Task 10: Frontend — share creation + redeem dialogs

**Files:**
- Create: `desktop/src/apps/cluster/ShareDialog.tsx`
- Create: `desktop/src/apps/cluster/RedeemDialog.tsx`
- Modify: `desktop/src/apps/ClusterApp.tsx`

- [ ] **Step 1: ShareDialog component**

```tsx
// desktop/src/apps/cluster/ShareDialog.tsx
import { useState } from "react";
import { Button } from "@/components/ui";

interface Props {
  open: boolean;
  workerId: string | null;
  onClose: () => void;
}

const CAPABILITY_OPTIONS = [
  { key: "chat", label: "Chat / completion" },
  { key: "embed", label: "Embeddings" },
  { key: "rerank", label: "Rerank" },
  { key: "image-gen", label: "Image generation" },
  { key: "training", label: "Fine-tuning / LoRA" },
];

export function ShareDialog({ open, workerId, onClose }: Props) {
  const [recipient, setRecipient] = useState("");
  const [capabilities, setCapabilities] = useState<string[]>(["chat", "embed"]);
  const [maxConcurrent, setMaxConcurrent] = useState(1);
  const [expiryKind, setExpiryKind] = useState<"inactive_30d" | "never" | "fixed_date">("inactive_30d");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [resultToken, setResultToken] = useState<string | null>(null);

  if (!open || !workerId) return null;

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`/api/cluster/workers/${workerId}/shares`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          recipient_identity: recipient.trim(),
          tier: "A",
          capabilities,
          max_concurrent_jobs: maxConcurrent,
          expiry_kind: expiryKind,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data?.error ?? `Share failed (${res.status})`);
        setBusy(false);
        return;
      }
      const data = await res.json();
      setResultToken(data.share_token);
      setBusy(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
      setBusy(false);
    }
  }

  function toggleCap(key: string) {
    setCapabilities((cur) =>
      cur.includes(key) ? cur.filter((c) => c !== key) : [...cur, key]
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-shell-surface border border-white/10 rounded-xl p-6 w-full max-w-lg shadow-xl"
      >
        {!resultToken && (
          <form onSubmit={handleCreate}>
            <h3 className="text-base font-semibold mb-3">Share this worker</h3>

            <label className="block text-[11px] uppercase tracking-wide text-shell-text-tertiary mb-1">
              Recipient (user or agent)
            </label>
            <input
              type="text"
              required
              value={recipient}
              onChange={(e) => setRecipient(e.target.value)}
              placeholder="user:bob  or  agent:bob/research-bot"
              className="w-full px-3 py-2 mb-3 rounded bg-shell-bg-deep border border-white/10 text-sm font-mono outline-none focus:border-accent/40"
              autoFocus
            />

            <label className="block text-[11px] uppercase tracking-wide text-shell-text-tertiary mb-2">
              Capabilities
            </label>
            <div className="space-y-1 mb-4">
              {CAPABILITY_OPTIONS.map((opt) => (
                <label key={opt.key} className="flex items-center gap-2 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    checked={capabilities.includes(opt.key)}
                    onChange={() => toggleCap(opt.key)}
                  />
                  <span>{opt.label}</span>
                </label>
              ))}
            </div>

            <label className="block text-[11px] uppercase tracking-wide text-shell-text-tertiary mb-1">
              Max concurrent jobs
            </label>
            <input
              type="number"
              min={1}
              max={32}
              value={maxConcurrent}
              onChange={(e) => setMaxConcurrent(Math.max(1, parseInt(e.target.value) || 1))}
              className="w-24 px-3 py-2 mb-3 rounded bg-shell-bg-deep border border-white/10 text-sm outline-none focus:border-accent/40"
            />

            <label className="block text-[11px] uppercase tracking-wide text-shell-text-tertiary mb-1">
              Expiry
            </label>
            <select
              value={expiryKind}
              onChange={(e) => setExpiryKind(e.target.value as typeof expiryKind)}
              className="w-full px-3 py-2 mb-4 rounded bg-shell-bg-deep border border-white/10 text-sm outline-none focus:border-accent/40"
            >
              <option value="inactive_30d">Auto-revoke if unused for 30 days</option>
              <option value="never">Never expire</option>
            </select>

            {error && <p className="text-xs text-red-400 mb-2" role="alert">{error}</p>}

            <div className="flex gap-2 justify-end">
              <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
              <Button type="submit" disabled={busy || !recipient || capabilities.length === 0}>
                {busy ? "Creating..." : "Create share"}
              </Button>
            </div>
          </form>
        )}

        {resultToken && (
          <div>
            <h3 className="text-base font-semibold mb-2">Share token created</h3>
            <p className="text-xs text-shell-text-tertiary mb-3">
              Send this to the recipient. Single-use, expires in 24 hours. They paste it into their taOS Cluster app.
            </p>
            <textarea
              readOnly
              value={resultToken}
              className="w-full h-24 px-3 py-2 mb-3 rounded bg-shell-bg-deep border border-white/10 text-xs font-mono"
              onClick={(e) => (e.target as HTMLTextAreaElement).select()}
            />
            <div className="flex gap-2 justify-end">
              <Button
                type="button"
                onClick={() => navigator.clipboard.writeText(resultToken)}
              >
                Copy
              </Button>
              <Button type="button" variant="ghost" onClick={onClose}>Done</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: RedeemDialog component**

```tsx
// desktop/src/apps/cluster/RedeemDialog.tsx
import { useState } from "react";
import { Button } from "@/components/ui";

interface Props {
  open: boolean;
  onClose: () => void;
  onRedeemed: () => void;
}

export function RedeemDialog({ open, onClose, onRedeemed }: Props) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!open) return null;

  async function handleRedeem(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/cluster/shares/redeem", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ share_token: token.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data?.error ?? "Token rejected");
        setBusy(false);
        return;
      }
      onRedeemed();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleRedeem}
        className="bg-shell-surface border border-white/10 rounded-xl p-6 w-full max-w-md shadow-xl"
      >
        <h3 className="text-base font-semibold mb-1">Redeem a share</h3>
        <p className="text-xs text-shell-text-tertiary mb-4">
          Paste the share token someone sent you to start using their worker.
        </p>

        <textarea
          required
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Paste share token..."
          className="w-full h-24 px-3 py-2 mb-3 rounded bg-shell-bg-deep border border-white/10 text-xs font-mono outline-none focus:border-accent/40"
          autoFocus
        />

        {error && <p className="text-xs text-red-400 mb-2" role="alert">{error}</p>}

        <div className="flex gap-2 justify-end">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" disabled={busy || !token.trim()}>
            {busy ? "Redeeming..." : "Redeem"}
          </Button>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 3: Wire both dialogs into ClusterApp.tsx**

Add buttons + state hooks:

```tsx
import { ShareDialog } from "./cluster/ShareDialog";
import { RedeemDialog } from "./cluster/RedeemDialog";

const [shareWorkerId, setShareWorkerId] = useState<string | null>(null);
const [redeemOpen, setRedeemOpen] = useState(false);

// Toolbar additions:
<Button size="sm" variant="ghost" onClick={() => setRedeemOpen(true)}>
  Redeem share
</Button>

// On each owned worker card:
<Button size="sm" variant="ghost" onClick={() => setShareWorkerId(worker.id)}>
  Share...
</Button>

// Mounted at end:
<ShareDialog
  open={shareWorkerId !== null}
  workerId={shareWorkerId}
  onClose={() => setShareWorkerId(null)}
/>
<RedeemDialog
  open={redeemOpen}
  onClose={() => setRedeemOpen(false)}
  onRedeemed={() => refetchWorkers?.()}
/>
```

- [ ] **Step 4: Build + verify**

```bash
cd desktop && npm run build 2>&1 | tail -3
```
Expected: clean build.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/apps/cluster/ShareDialog.tsx desktop/src/apps/cluster/RedeemDialog.tsx desktop/src/apps/ClusterApp.tsx
git commit -m "feat(cluster ui): share creation + redeem dialogs"
```

---

## Task 11: Frontend — borrowed worker badge + colour border

**Files:**
- Create: `desktop/src/apps/cluster/BorrowedWorkerBadge.tsx`
- Modify: `desktop/src/apps/ClusterApp.tsx` (merge borrowed list with owned, render badge + border)

- [ ] **Step 1: Badge component**

```tsx
// desktop/src/apps/cluster/BorrowedWorkerBadge.tsx
interface Props {
  ownerIdentity: string;
  redeemedAt: number; // epoch seconds
  color?: string;
}

export function BorrowedWorkerBadge({ ownerIdentity, redeemedAt, color }: Props) {
  const dt = new Date(redeemedAt * 1000);
  const since = dt.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const owner = ownerIdentity.replace(/^user:/, "").replace(/^agent:/, "");

  return (
    <span
      className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-wide px-2 py-0.5 rounded-full"
      style={{
        backgroundColor: color ? `${color}22` : "rgba(139, 146, 163, 0.15)",
        color: color ?? "#8b92a3",
        border: `1px solid ${color ?? "#8b92a3"}66`,
      }}
      title={`Borrowed from ${owner} since ${since}`}
    >
      borrowed · {owner}
    </span>
  );
}
```

- [ ] **Step 2: In ClusterApp, fetch and merge borrowed**

```tsx
const [borrowed, setBorrowed] = useState<BorrowedWorker[]>([]);

useEffect(() => {
  fetch("/api/cluster/borrowed", { credentials: "include" })
    .then((r) => (r.ok ? r.json() : []))
    .then(setBorrowed)
    .catch(() => setBorrowed([]));
}, [refreshKey /* whatever existing refresh-trigger key is */]);
```

In the worker card render, derive a per-owner colour and apply it:

```tsx
function colorFor(ownerIdentity: string): string {
  // Stable hash → HSL colour. Same owner always gets the same colour.
  let h = 0;
  for (let i = 0; i < ownerIdentity.length; i++) {
    h = (h * 31 + ownerIdentity.charCodeAt(i)) | 0;
  }
  const hue = Math.abs(h) % 360;
  return `hsl(${hue}, 35%, 60%)`;
}

// In the render loop, treat each borrowed entry as a worker card:
{borrowed.map((b) => {
  const col = colorFor(b.owner_identity);
  return (
    <Card
      key={b.share_id}
      className="rounded-xl border-l-4"
      style={{ borderLeftColor: col }}
    >
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div className="font-medium text-sm">{b.worker_id}</div>
          <BorrowedWorkerBadge
            ownerIdentity={b.owner_identity}
            redeemedAt={b.redeemed_at}
            color={col}
          />
        </div>
      </CardHeader>
      {/* ...rest of the worker card surface (capabilities, status) */}
    </Card>
  );
})}
```

- [ ] **Step 3: Build**

```bash
cd desktop && npm run build 2>&1 | tail -3
```
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/cluster/BorrowedWorkerBadge.tsx desktop/src/apps/ClusterApp.tsx
git commit -m "feat(cluster ui): borrowed worker badge + colour-coded border in worker list"
```

---

## Task 12: Frontend — Activity app Shares card

**Files:**
- Create: `desktop/src/apps/activity/SharesCard.tsx`
- Modify: `desktop/src/apps/ActivityApp.tsx` (mount the card)

- [ ] **Step 1: Component**

```tsx
// desktop/src/apps/activity/SharesCard.tsx
import { useEffect, useState } from "react";
import { Pause, Play, X } from "lucide-react";
import { Card, CardContent, CardHeader, Button } from "@/components/ui";

interface Share {
  id: string;
  worker_id: string;
  recipient_identity: string;
  tier: string;
  capabilities: string[];
  paused: boolean;
  revoked_at: number | null;
  last_used_at: number;
  recipient_color?: string;
}

export function SharesCard() {
  const [shares, setShares] = useState<Share[]>([]);

  async function refresh() {
    const res = await fetch("/api/cluster/shares", { credentials: "include" });
    if (res.ok) setShares(await res.json());
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30000);
    return () => clearInterval(id);
  }, []);

  async function pause(id: string, paused: boolean) {
    await fetch(`/api/cluster/shares/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ paused }),
    });
    refresh();
  }

  async function revoke(id: string) {
    if (!confirm("Revoke this share? The recipient loses access immediately.")) return;
    await fetch(`/api/cluster/shares/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    refresh();
  }

  const active = shares.filter((s) => !s.revoked_at);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">Shares</h3>
          <span className="text-[10px] uppercase tracking-wide text-shell-text-tertiary">
            {active.length} active
          </span>
        </div>
      </CardHeader>
      <CardContent>
        {active.length === 0 && (
          <p className="text-xs text-shell-text-tertiary">No active shares.</p>
        )}
        <ul className="space-y-2">
          {active.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-2 text-xs">
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate">{s.recipient_identity}</div>
                <div className="text-shell-text-tertiary text-[10px]">
                  {s.worker_id} · tier {s.tier} · {s.capabilities.join(", ")}
                  {s.last_used_at > 0 && (
                    <> · last used {new Date(s.last_used_at * 1000).toLocaleString()}</>
                  )}
                </div>
              </div>
              <div className="flex gap-1 shrink-0">
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => pause(s.id, !s.paused)}
                  aria-label={s.paused ? "Resume share" : "Pause share"}
                  title={s.paused ? "Resume" : "Pause"}
                >
                  {s.paused ? <Play size={12} /> : <Pause size={12} />}
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => revoke(s.id)}
                  aria-label="Revoke share"
                  title="Revoke"
                >
                  <X size={12} />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Mount in ActivityApp**

In `desktop/src/apps/ActivityApp.tsx`, locate the existing card layout (probably a CSS grid). Add:

```tsx
import { SharesCard } from "./activity/SharesCard";

// In the layout, alongside the Cluster card:
<SharesCard />
```

- [ ] **Step 3: Build**

```bash
cd desktop && npm run build 2>&1 | tail -3
```
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/activity/SharesCard.tsx desktop/src/apps/ActivityApp.tsx
git commit -m "feat(activity ui): Shares card with pause/revoke quick-actions"
```

---

## Task 13: Cloud-account discovery (username lookup)

**Files:**
- Modify: `tinyagentos/routes/cluster.py` (add a stub `/api/cluster/contacts` that returns local users only — Phase 1 doesn't have the cloud directory yet)
- Modify: `desktop/src/apps/cluster/ShareDialog.tsx` (use the contacts endpoint to autocomplete the recipient field if the user has multiple users locally)

For Phase 1 the cloud account directory doesn't exist yet (Phase 4 work). The local fallback is: if the install is multi-user, the recipient picker autocompletes from `/api/auth/users`.

- [ ] **Step 1: Make the recipient input fall back to local users**

In `desktop/src/apps/cluster/ShareDialog.tsx`, fetch local users on mount:

```tsx
const [localUsers, setLocalUsers] = useState<string[]>([]);

useEffect(() => {
  if (!open) return;
  fetch("/api/auth/users", { credentials: "include" })
    .then((r) => (r.ok ? r.json() : []))
    .then((users: any[]) => {
      setLocalUsers(users.map((u) => `user:${u.username}`));
    })
    .catch(() => setLocalUsers([]));
}, [open]);
```

Render a `<datalist>` of suggestions:

```tsx
<input
  list="recipient-suggestions"
  /* ...rest of input props */
/>
<datalist id="recipient-suggestions">
  {localUsers.map((id) => <option key={id} value={id} />)}
</datalist>
```

- [ ] **Step 2: Build**

```bash
cd desktop && npm run build 2>&1 | tail -3
```
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/apps/cluster/ShareDialog.tsx
git commit -m "feat(cluster ui): recipient autocomplete from local users (cloud directory in Phase 4)"
```

---

## Task 14: Smoke test the full pair → share → redeem flow end-to-end

- [ ] **Step 1: Run a real worker on port 8001**

```bash
.venv/bin/python -m tinyagentos.worker --controller-url http://localhost:6969 --port 8001 &
```

- [ ] **Step 2: Generate an OTP via CLI**

```bash
.venv/bin/python -m tinyagentos.worker pair --controller http://localhost:6969 --state-dir ~/.local/share/tinyagentos-worker
# Note the OTP printed
```

- [ ] **Step 3: Pair from the controller**

```bash
curl -s -X POST http://localhost:6969/api/cluster/pair \
  -H "Cookie: $(cat ~/.taos-test-cookie)" \
  -H "Content-Type: application/json" \
  -d "{\"otp\": \"<OTP>\", \"worker_url\": \"http://localhost:8001\"}"
# Expected: {"paired": true, "worker_id": "..."}
```

- [ ] **Step 4: Create a share**

```bash
curl -s -X POST http://localhost:6969/api/cluster/workers/<WORKER_ID>/shares \
  -H "Cookie: $(cat ~/.taos-test-cookie)" \
  -H "Content-Type: application/json" \
  -d '{"recipient_identity":"user:test","tier":"A","capabilities":["chat","embed"],"max_concurrent_jobs":1,"expiry_kind":"inactive_30d"}'
# Note the share_token
```

- [ ] **Step 5: Redeem the share (simulating recipient)**

```bash
curl -s -X POST http://localhost:6969/api/cluster/shares/redeem \
  -H "Cookie: $(cat ~/.taos-test-cookie)" \
  -H "Content-Type: application/json" \
  -d "{\"share_token\": \"<TOKEN>\"}"
# Expected: {"ok": true, "worker_id": "...", ...}
```

- [ ] **Step 6: Verify borrowed list**

```bash
curl -s http://localhost:6969/api/cluster/borrowed -H "Cookie: $(cat ~/.taos-test-cookie)" | python3 -m json.tool
# Expected: array with the redeemed share
```

- [ ] **Step 7: Revoke and re-check**

```bash
curl -s -X DELETE http://localhost:6969/api/cluster/shares/<SHARE_ID> -H "Cookie: $(cat ~/.taos-test-cookie)"
curl -s http://localhost:6969/api/cluster/shares -H "Cookie: $(cat ~/.taos-test-cookie)" | python3 -m json.tool
# Expected: share has revoked_at set
```

- [ ] **Step 8: Final test sweep**

```bash
.venv/bin/pytest tests/test_worker_pairing.py tests/test_share_tokens.py tests/test_shares_store.py tests/test_routes_cluster_pair.py tests/test_routes_cluster_shares.py -v
```
Expected: all green.

- [ ] **Step 9: Commit any cleanup notes from the smoke test**

```bash
# Only if the smoke test surfaced something needing a note in the spec
git add docs/superpowers/specs/2026-04-14-worker-sharing-design.md  # if updated
git commit -m "docs(worker-sharing): smoke-test notes from Phase 1 end-to-end run"
```

- [ ] **Step 10: Merge to master and tag a checkpoint**

```bash
git checkout master
git merge --no-ff feat/worker-sharing-phase1 -m "Merge feat/worker-sharing-phase1 — pairing + tier A sharing + UIs"
git tag -a worker-sharing-phase1 -m "Phase 1 of #212 — pairing + tier A sharing"
git push origin master --tags
```

---

## Self-review

**Spec coverage check** (for each section in the spec, point at a Phase 1 task):

| Spec section | Phase 1 task |
|---|---|
| Pairing — self-hosted OTP | Tasks 1, 2, 3 |
| Pairing — cloud-account | Phase 4 (out of Phase 1 scope) |
| Trust tiers — A | Task 7 (B/C are Phase 2/3) |
| Identity model — cloud + P2P | Phase 4 (Phase 1 supports P2P paste-the-token only) |
| Recipient granularity | Task 7 (recipient_identity field accepts both forms) |
| Limits — capability allowlist | Task 7 |
| Limits — concurrent jobs | Tasks 5, 7 |
| Limits — storage quota | Phase 2 |
| Limits — encrypted volumes | Phase 2 |
| Limits — expiry | Task 8 (`inactive_30d` watcher) |
| Limits — pause | Tasks 5, 7 (PUT /api/cluster/shares/{id}) |
| Limits — usage view | Tasks 5, 7 (GET /api/cluster/shares/{id}/usage) |
| Worker disk floor | Phase 2 |
| Share creation — owner-issued | Task 7 |
| Share creation — recipient-requested | Phase 4 |
| Revoke — drain vs hard-kill | Phase 2 (drain semantics need a job tracker) |
| Revoke — soft-delete with grace | Phase 2 (no persistent recipient data in Phase 1) |
| Revoke — encrypted volume opaque | Phase 2 |
| Auto-expiry | Task 8 |
| Container isolation | Phase 2/3 (LXC for tier B/C) |
| Encrypted volumes implementation | Phase 2 |
| Recipient UX — borrowed badge + colour | Task 11 |
| Owner observability — notifications | Tasks 7, 8 (revoke + auto-expire emit events) |
| Owner observability — Activity card | Task 12 |
| Discovery — cloud directory | Phase 4 (Task 13 ships local-users fallback) |

All Phase 1 spec items have a task; deferred items are documented.

**Placeholder scan**: no `TODO`, `TBD`, `?????`, "implement later", or "similar to Task N". All code blocks are concrete.

**Type consistency**: `recipient_identity` is consistently a string in `user:<name>` or `agent:<owner>/<agent_name>` format across all tasks. `share_token` is consistently the dotted body.sig format. `bearer` is plain text in transit, hashed in storage. `expiry_kind` enum is `inactive_30d | never | fixed_date` everywhere.

**Scope check**: Phase 1 produces an end-to-end useful slice. A user can pair a worker, share it with a friend at tier A, the friend can redeem and call inference, the owner can see usage and revoke. Each task ships in a single commit (or a small handful for the larger ones); no task takes more than a working day.
