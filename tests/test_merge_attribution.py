#!/usr/bin/env python3
"""Tests for merge attribution audit and reconciliation.

Acceptance proofs for fix-forward #2579:
(a) A squash-merged PR after the cutoff with no audit line must exit 1
    naming that PR/sha.
(b) The GREEN case must be produced BY gate_merge.sh -- drive the wrapper
    (stub gh on PATH) and reconcile its real output. A hand-written fixture
    hides exactly the producer/consumer SHA mismatch this fixes.
(c) Reconcile on mergeCommit from the GitHub API (`gh pr view <n>
    --json mergeCommit`), not on `git log --merges`. Enumate from
    `gh pr list --state merged` so squash merges are visible.
(d) A cutoff keeps pre-adoption merges out of scope.

Acceptance proof for fix-forward #2586:
(e) A cutoff given as a git SHA whose committer offset is east of UTC must
    still keep an in-scope merge in scope. The cutoff and `mergedAt` arrive in
    different formats, so they have to be compared as instants; every other
    test here passes a UTC literal, which never reaches that branch.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = str(REPO_ROOT / "scripts/check_merge_attribution.py")
GATE = str(REPO_ROOT / "scripts/gate_merge.sh")

# ---------------------------------------------------------------------------
# Stub gh CLI
# ---------------------------------------------------------------------------
_STUB_GH = r'''#!/usr/bin/env python3
"""Stub gh CLI for merge-attribution tests.

Reads PR data from the JSON file at $GH_STUB_CONFIG and responds to
pr list, pr merge, pr view, and repo view commands with --jq filters.
"""
import sys, json, os

CONFIG_FILE = os.environ.get("GH_STUB_CONFIG", "/dev/null")

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"prs": [], "repo": "test/test", "default_pr": {}}

def apply_jq(obj, filt):
    """Apply a simple jq filter like '.field.subfield'."""
    if not filt:
        return None
    parts = filt.lstrip(".").split(".")
    val = obj
    for p in parts:
        if p == "":
            continue
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    return val

def find_pr(cfg, pr_num):
    for pr in cfg.get("prs", []):
        if str(pr.get("number")) == str(pr_num):
            return pr
    return cfg.get("default_pr", {})

def main():
    args = sys.argv[1:]
    if len(args) < 1:
        sys.exit(1)
    cfg = load_config()

    if len(args) >= 2 and args[0] == "pr":
        sub = args[1]

        if sub == "list":
            print(json.dumps(cfg.get("prs", [])))
            sys.exit(0)

        if sub == "merge":
            sys.exit(0)

        if sub == "view":
            # Collect --json fields, --jq filter, and the PR ref.
            json_fields = []
            jq_filter = None
            pr_num = None
            i = 2
            while i < len(args):
                a = args[i]
                if a == "--json" and i + 1 < len(args):
                    json_fields = args[i + 1].split(",")
                    i += 2
                elif a == "--jq" and i + 1 < len(args):
                    jq_filter = args[i + 1]
                    i += 2
                elif a == "--repo" and i + 1 < len(args):
                    i += 2
                elif not a.startswith("-"):
                    if pr_num is None:
                        pr_num = a
                    i += 1
                else:
                    i += 1

            pr_data = find_pr(cfg, pr_num)

            if jq_filter:
                val = apply_jq(pr_data, jq_filter)
                if val is None:
                    print("null")
                elif isinstance(val, bool):
                    print("true" if val else "false")
                elif isinstance(val, (dict, list)):
                    print(json.dumps(val))
                else:
                    print(val)
            else:
                if json_fields:
                    resp = {f: pr_data.get(f) for f in json_fields}
                    print(json.dumps(resp))
                else:
                    print(json.dumps(pr_data))
            sys.exit(0)

    if len(args) >= 2 and args[0] == "repo":
        if args[1] == "view":
            if len(args) > 2:
                # Might have --json and --jq
                jq_filter = None
                for i, a in enumerate(args):
                    if a == "--jq" and i + 1 < len(args):
                        jq_filter = args[i + 1]
                if jq_filter:
                    val = apply_jq({"nameWithOwner": cfg.get("repo", "test/test")}, jq_filter)
                    if val is None:
                        print("null")
                    else:
                        print(val)
                else:
                    print(json.dumps({"nameWithOwner": cfg.get("repo", "test/test")}))
            else:
                print(json.dumps({"nameWithOwner": cfg.get("repo", "test/test")}))
            sys.exit(0)

    sys.exit(1)

if __name__ == "__main__":
    main()
'''


# ---------------------------------------------------------------------------
# Repo helpers
# ---------------------------------------------------------------------------

def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True, check=True)
    # Add a remote so gh can auto-detect; stub ignores it.
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/test-org/test-repo.git"],
                   cwd=repo, capture_output=True, check=True)


def _commit_file(
    repo: Path,
    rel_path: str,
    content: str,
    message: str,
    committer_date: str | None = None,
) -> str:
    """Create a file, commit it, and return the commit SHA.

    *committer_date* (any value git accepts in ``GIT_COMMITTER_DATE``) pins the
    committer timestamp, including its UTC offset -- that offset is what
    ``git log --format=%cI`` reports back for a SHA cutoff.
    """
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    env = os.environ.copy()
    if committer_date is not None:
        env["GIT_COMMITTER_DATE"] = committer_date
        env["GIT_AUTHOR_DATE"] = committer_date
    subprocess.run(["git", "add", rel_path], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo, capture_output=True, check=True, env=env,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestMergeAttributionReconciliation:
    """Acceptance proofs for fix-forward #2579.

    (a) A squash-merged PR after the cutoff with no audit line is RED.
    (b) The green case is produced by gate_merge.sh with a stub gh on PATH.
    (c) The checker enumerates from `gh pr list` (not `git log --merges`).
    (d) A cutoff keeps pre-adoption merges out of scope.
    (e) A SHA cutoff with a non-UTC committer offset keeps in-scope merges in
        scope (fix-forward #2586).
    """

    # ---- shared helpers (in the same class that calls them) ----

    @staticmethod
    def _write_stub_gh(tmp_path: Path) -> Path:
        """Write a stub `gh` binary and return its path."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        gh_path = bin_dir / "gh"
        gh_path.write_text(_STUB_GH, encoding="utf-8")
        gh_path.chmod(0o755)
        return gh_path

    @staticmethod
    def _write_config(tmp_path: Path, prs: list[dict], repo: str = "test-org/test-repo") -> Path:
        """Write a stub gh config file and return its path."""
        config_file = tmp_path / "gh_config.json"
        config = {
            "repo": repo,
            "prs": prs,
            "default_pr": prs[0] if prs else {},
        }
        config_file.write_text(json.dumps(config), encoding="utf-8")
        return config_file

    @staticmethod
    def _make_env(tmp_path: Path, config_file: Path, extra: dict | None = None) -> dict:
        """Build an environment with stub gh on PATH and isolated HOME."""
        env = os.environ.copy()
        # Prepend stub bin dir so `gh` resolves to our stub.
        env["PATH"] = f"{tmp_path / 'bin'}:{env['PATH']}"
        # Isolate HOME so ~/.taos-team/gate_merge.sh does not exist -> gate
        # falls through to the bare gh pr merge path we can stub.
        env["HOME"] = str(tmp_path / "home")
        # Stub reads PR data from here.
        env["GH_STUB_CONFIG"] = str(config_file)
        if extra:
            env.update(extra)
        return env

    @staticmethod
    def _run_checker(
        tmp_path: Path,
        env: dict,
        cutoff: str,
        extra_args: list[str] | None = None,
    ) -> subprocess.CompletedProcess:
        """Run check_merge_attribution.py with the given env and cutoff."""
        return subprocess.run(
            [
                sys.executable, CHECKER,
                "--repo", str(tmp_path / "repo"),
                "--audit-file", str(tmp_path / "audit.jsonl"),
                "--cutoff", cutoff,
                *(extra_args or []),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    # ---- acceptance (a): unattributed squash merge goes RED ----

    def test_unattributed_squash_merge_fails_reconciliation(self, tmp_path: Path) -> None:
        """(a) A squash-merged PR after the cutoff with NO audit line is RED.

        This is the THREAT case: a stolen token doing exactly what the card
        describes -- a squash merge with no audit entry -- must be caught.
        On the original code it reconciles clean because the checker reads
        `git log --merges` (which never sees squash merges) and logs the
        headRefOid while the checker expects the merge-commit SHA.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")

        # A commit that represents the squash-merge result on the base branch.
        squash_sha = _commit_file(repo, "feat.txt", "feature\n", "squash merge PR #42")

        config = {
            "prs": [
                {
                    "number": 42,
                    "mergeCommit": {"oid": squash_sha},
                    "mergedAt": "2026-08-28T12:00:00Z",
                    "mergedBy": {"login": "stolen-token"},
                },
            ],
        }
        config_file = self._write_config(tmp_path, config["prs"])
        self._write_stub_gh(tmp_path)

        env = self._make_env(tmp_path, config_file)
        # Audit file is empty -- no entry written by gate_merge.sh.
        audit_file = tmp_path / "audit.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text("", encoding="utf-8")

        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")

        assert result.returncode == 1
        assert "42" in result.stdout
        assert squash_sha[:12] in result.stdout

    # ---- acceptance (b): green case produced by gate_merge.sh ----

    def test_attributed_merge_produced_by_gate_passes_reconciliation(self, tmp_path: Path) -> None:
        """(b) The GREEN case is produced BY gate_merge.sh, not hand-written.

        Drives the wrapper with a stub gh on PATH, captures the audit entry
        it writes, and reconciles its real output. A hand-written fixture
        (the old _write_audit) is what hid the SHA mismatch -- this test
        proves the producer and consumer agree on mergeCommit.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")

        # A commit that represents the merge result.
        merge_sha = _commit_file(repo, "feat.txt", "feature\n", "merge PR #42")

        config = {
            "prs": [
                {
                    "number": 42,
                    "mergeCommit": {"oid": merge_sha},
                    "mergedAt": "2026-08-28T12:00:00Z",
                    "mergedBy": {"login": "test-agent"},
                },
            ],
        }
        config_file = self._write_config(tmp_path, config["prs"])
        self._write_stub_gh(tmp_path)

        audit_file = tmp_path / "audit.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text("", encoding="utf-8")
        env = self._make_env(tmp_path, config_file, extra={"FLEET_AUDIT_LOG": str(audit_file)})

        # Drive the wrapper -- stub gh makes the merge + view calls succeed.
        gate_result = subprocess.run(
            ["bash", GATE, "42", "tsk-test", "test note"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert gate_result.returncode == 0, gate_result.stderr

        # The audit entry must exist and contain the real mergeCommit OID,
        # NOT the headRefOid -- proving the producer reads the same key.
        assert audit_file.exists()
        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["sha"] == merge_sha
        assert entry["pr"] == 42

        # Reconcile -- should pass.
        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")
        assert result.returncode == 0, result.stdout + result.stderr

    # ---- acceptance (d): cutoff excludes pre-adoption merges ----

    def test_cutoff_excludes_pre_adoption_merges(self, tmp_path: Path) -> None:
        """(d) A cutoff keeps pre-adoption merges out of scope.

        Two merged PRs: one before the cutoff (no audit, but out of scope)
        and one after (no audit, in scope -> RED). Only the in-scope PR
        is reported.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")

        old_sha = _commit_file(repo, "old.txt", "old\n", "old merge PR #41")
        new_sha = _commit_file(repo, "new.txt", "new\n", "new merge PR #42")

        config = {
            "prs": [
                {
                    "number": 41,
                    "mergeCommit": {"oid": old_sha},
                    "mergedAt": "2026-08-20T12:00:00Z",
                    "mergedBy": {"login": "bot"},
                },
                {
                    "number": 42,
                    "mergeCommit": {"oid": new_sha},
                    "mergedAt": "2026-08-28T12:00:00Z",
                    "mergedBy": {"login": "bot"},
                },
            ],
        }
        config_file = self._write_config(tmp_path, config["prs"])
        self._write_stub_gh(tmp_path)

        env = self._make_env(tmp_path, config_file)
        audit_file = tmp_path / "audit.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text("", encoding="utf-8")

        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")

        assert result.returncode == 1
        # Only PR #42 (after cutoff) should be reported. Assert on the exact
        # "#41"/"#42" PR-reference token the checker prints
        # (f"#{pr_num} {short}"), not the bare digits: a runtime-generated
        # commit sha is asserted present right alongside it, and a bare "41"
        # or "42" can coincidentally appear inside THAT hex sha, making the
        # assertion flake independent of whether PR #41 was actually reported.
        assert "#42" in result.stdout
        assert "#41" not in result.stdout
        assert old_sha[:12] not in result.stdout
        assert new_sha[:12] in result.stdout

    def test_bare_pr_number_assertion_is_not_sha_safe(self) -> None:
        """Pins WHY the two tests above assert "#41"/"#42", not the bare
        digits. The fixture shas in this file are generated at runtime by
        real git commits, so the IN-SCOPE PR's own sha can coincidentally
        contain the excluded PR's number as a hex substring -- CI hit this
        three times in one hour (#2798 shard 3.12/3, #2800 shard 3.13/3)
        because "41" happened to land inside PR #42's sha. This test fixes
        the collision instead of hoping for one, so it is deterministic, not
        probabilistic.
        """
        # Stand-in for the checker's real stdout: PR #41 correctly excluded
        # (cutoff/pre-adoption), PR #42 in scope and unmatched. The #42 sha
        # is chosen to contain "41" as a hex-digit coincidence -- exactly the
        # collision a real run hit by chance.
        stdout = "UNMATCHED MERGE: #42 a41bcdef0123 -- no audit entry found\n"

        # A bare-digit assertion here (`assert "41" not in stdout`) fails
        # even though PR #41 was never reported -- "41" matches inside #42's
        # sha, not PR #41's number. The fixed pattern checks the PR-reference
        # token the checker actually prints (f"#{pr_num} {short}"), which
        # cannot collide with a hex sha substring the way a bare number can.
        assert "#41" not in stdout

    # ---- acceptance (c): checker enumerates from gh API, not git log ----

    def test_checker_does_not_rely_on_git_log_merges(self, tmp_path: Path) -> None:
        """(c) The checker enumerates merges from `gh pr list`, not git log.

        A repo where `git log --merges` is empty (squash-style) but the
        GitHub API reports a merged PR must still be checked. This proves
        the check is driven by the API, not by git history.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")

        # Create a two-parent merge commit so `git log --merges` WOULD see it,
        # then verify the checker does NOT use git log --merges by confirming
        # that a PR known to the API but absent from git history is caught.
        # We simulate: API reports PR #42 (mergeCommit = sha), but the commit
        # is NOT in git log --merges (squash: single parent).
        squash_sha = _commit_file(repo, "feat.txt", "feature\n", "squash PR #42")

        config = {
            "prs": [
                {
                    "number": 42,
                    "mergeCommit": {"oid": squash_sha},
                    "mergedAt": "2026-08-28T12:00:00Z",
                    "mergedBy": {"login": "actor"},
                },
            ],
        }
        config_file = self._write_config(tmp_path, config["prs"])
        self._write_stub_gh(tmp_path)

        env = self._make_env(tmp_path, config_file)
        audit_file = tmp_path / "audit.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text("", encoding="utf-8")

        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")

        # git log --merges would NOT find the squash (single parent), so if
        # the checker still flags it, it is reading from the API not git.
        assert result.returncode == 1
        assert "42" in result.stdout

    # ---- acceptance (e): SHA cutoff carrying a non-UTC offset ----

    def test_sha_cutoff_with_east_offset_keeps_in_scope_merge(self, tmp_path: Path) -> None:
        """(e) A git-SHA cutoff committed east of UTC must not drop merges.

        `_resolve_cutoff` resolves a SHA with `git log --format=%cI`, which
        carries the COMMITTER'S LOCAL OFFSET, while `mergedAt` from the API is
        always UTC with a trailing `Z`. Comparing those two as strings orders
        `2026-08-28T13:00:00Z` BEFORE `2026-08-28T14:00:00+02:00` even though
        both name the same day and the merge is an hour AFTER the 12:00Z
        cutoff -- so an in-scope merge is silently dropped from the audit and
        its missing audit line is never reported. The window opened is the
        size of the offset. Every other test here passes a UTC literal, which
        never reaches the `%cI` branch.

        PR #41 (11:00Z, genuinely before the cutoff) is the control: the fix
        must narrow the comparison, not delete it.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")

        # The cutoff commit: 14:00 in +02:00 == 12:00Z.
        cutoff_sha = _commit_file(
            repo, "cutoff.txt", "cutoff\n", "adopt merge attribution",
            committer_date="2026-08-28T14:00:00+0200",
        )
        # Sanity: the committer offset really did survive into %cI, otherwise
        # this test would silently stop exercising the branch it exists for.
        cutoff_iso = subprocess.run(
            ["git", "log", "-1", "--format=%cI", cutoff_sha],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert cutoff_iso == "2026-08-28T14:00:00+02:00", cutoff_iso

        old_sha = _commit_file(repo, "old.txt", "old\n", "squash PR #41")
        new_sha = _commit_file(repo, "new.txt", "new\n", "squash PR #42")

        prs = [
            {
                # 11:00Z -- genuinely BEFORE the 12:00Z cutoff, out of scope.
                "number": 41,
                "mergeCommit": {"oid": old_sha},
                "mergedAt": "2026-08-28T11:00:00Z",
                "mergedBy": {"login": "bot"},
            },
            {
                # 13:00Z -- genuinely AFTER the 12:00Z cutoff, in scope, and
                # carrying no audit line, so it must be reported.
                "number": 42,
                "mergeCommit": {"oid": new_sha},
                "mergedAt": "2026-08-28T13:00:00Z",
                "mergedBy": {"login": "stolen-token"},
            },
        ]
        config_file = self._write_config(tmp_path, prs)
        self._write_stub_gh(tmp_path)

        env = self._make_env(tmp_path, config_file)
        audit_file = tmp_path / "audit.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text("", encoding="utf-8")

        result = self._run_checker(tmp_path, env, cutoff=cutoff_sha)

        assert result.returncode == 1, result.stdout + result.stderr
        assert "#42" in result.stdout
        assert new_sha[:12] in result.stdout
        # The control stays out of scope. Assert on "#41", not the bare
        # digits: new_sha (asserted present two lines up) is a runtime commit
        # sha that can coincidentally contain "41" as a substring, which would
        # fail this assertion even though PR #41 was correctly excluded.
        assert "#41" not in result.stdout
        assert old_sha[:12] not in result.stdout

    def test_unparseable_cutoff_is_an_error_not_an_empty_scope(self, tmp_path: Path) -> None:
        """A cutoff that is neither a git ref nor a timestamp must fail closed.

        Passing it through to a comparison it cannot satisfy would put every
        merge out of scope and report a clean audit, which is the fail-open
        this gate exists to prevent.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")
        squash_sha = _commit_file(repo, "feat.txt", "feature\n", "squash PR #42")

        prs = [
            {
                "number": 42,
                "mergeCommit": {"oid": squash_sha},
                "mergedAt": "2026-08-28T13:00:00Z",
                "mergedBy": {"login": "bot"},
            },
        ]
        config_file = self._write_config(tmp_path, prs)
        self._write_stub_gh(tmp_path)

        env = self._make_env(tmp_path, config_file)
        (tmp_path / "audit.jsonl").write_text("", encoding="utf-8")

        result = self._run_checker(tmp_path, env, cutoff="last-tuesday")

        assert result.returncode == 2, result.stdout + result.stderr
        assert "last-tuesday" in result.stderr

    def test_hung_gh_maps_to_exit_error(self, tmp_path: Path) -> None:
        """A `gh` that never returns must map to EXIT_ERROR, not hang CI."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        gh_path = bin_dir / "gh"
        gh_path.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
        gh_path.chmod(0o755)

        config_file = self._write_config(tmp_path, [])
        env = self._make_env(tmp_path, config_file)
        (tmp_path / "audit.jsonl").write_text("", encoding="utf-8")

        result = self._run_checker(
            tmp_path, env,
            cutoff="2026-08-28T00:00:00Z",
            extra_args=["--gh-timeout", "2"],
        )

        assert result.returncode == 2, result.stdout + result.stderr
        assert "timed out" in result.stderr

    def test_audit_line_survives_quotes_in_actor(self, tmp_path: Path) -> None:
        """gate_merge.sh must emit real JSON, not an interpolated string.

        `FLEET_ACTOR` is caller-supplied. Building the audit line by
        interpolating it into a hand-written `{...}` means one double quote
        emits a line the checker's JSON parser cannot read, and an unreadable
        audit line is an unattributed merge.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")
        merge_sha = _commit_file(repo, "feat.txt", "feature\n", "merge PR #42")

        prs = [
            {
                "number": 42,
                "mergeCommit": {"oid": merge_sha},
                "mergedAt": "2026-08-28T12:00:00Z",
                "mergedBy": {"login": 'we"ird'},
            },
        ]
        config_file = self._write_config(tmp_path, prs)
        self._write_stub_gh(tmp_path)

        audit_file = tmp_path / "audit.jsonl"
        audit_file.write_text("", encoding="utf-8")
        hostile_actor = 'agent"-\\x'
        env = self._make_env(tmp_path, config_file, extra={
            "FLEET_AUDIT_LOG": str(audit_file),
            "FLEET_ACTOR": hostile_actor,
        })

        gate_result = subprocess.run(
            ["bash", GATE, "42", "tsk-test", "test note"],
            capture_output=True, text=True, check=False, env=env,
        )
        assert gate_result.returncode == 0, gate_result.stderr

        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["actor"] == hostile_actor
        assert entry["sha"] == merge_sha
        assert entry["pr"] == 42
        assert entry["merged_by"] == 'we"ird'

        # And the entry still reconciles -- a mangled line would leave the
        # merge unmatched.
        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")
        assert result.returncode == 0, result.stdout + result.stderr

    # ---- review fold: audit entries that are valid JSON but not objects ----

    def test_non_object_audit_lines_are_skipped_not_crashed_on(self, tmp_path: Path) -> None:
        """A `null`/array/string/number audit line must be skipped, not crash.

        `json.loads` happily returns None, list, str and int, none of which
        have `.get`. Appending them and then reading `e.get("sha")` raises
        AttributeError, which `main()` does not map to EXIT_ERROR -- the run
        dies with a traceback and an exit code that reads as FAIL, so a real
        unmatched merge is indistinguishable from a malformed log.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")
        merge_sha = _commit_file(repo, "feat.txt", "feature\n", "merge PR #42")

        prs = [
            {
                "number": 42,
                "mergeCommit": {"oid": merge_sha},
                "mergedAt": "2026-08-28T12:00:00Z",
                "mergedBy": {"login": "bot"},
            },
        ]
        config_file = self._write_config(tmp_path, prs)
        self._write_stub_gh(tmp_path)
        env = self._make_env(tmp_path, config_file)

        # Every line here is valid JSON and none of them is an audit object.
        # The last line is the real entry, which must still be honoured.
        (tmp_path / "audit.jsonl").write_text(
            "null\n"
            '["not", "an", "object"]\n'
            '"just a string"\n'
            "42\n"
            + json.dumps({
                "actor": "bot", "repo": "test-org/test-repo", "pr": 42,
                "sha": merge_sha, "merged_by": "bot",
                "timestamp": "2026-08-28T12:00:00Z", "script": "gate_merge.sh",
            }) + "\n",
            encoding="utf-8",
        )

        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")

        assert "Traceback" not in result.stderr, result.stderr
        assert "AttributeError" not in result.stderr, result.stderr
        # The one real entry matches, so the audit is clean.
        assert result.returncode == 0, result.stdout + result.stderr
        # ...and the four junk lines were reported, not swallowed.
        assert result.stderr.count("WARN") == 4, result.stderr

    # ---- review fold: audit entries are scoped to the repository ----

    def test_audit_entry_from_another_repo_does_not_match(self, tmp_path: Path) -> None:
        """An audit entry for a different repo must not satisfy this merge.

        `gate_merge.sh` records `repo` alongside `sha`, and the default audit
        log (`~/.fleet/merge-audit.jsonl`) is one file shared by every repo the
        fleet merges. Matching on `sha` alone lets an entry written for another
        repo -- a fork or mirror carrying the same commit OID -- stand in as
        proof for a merge here that nobody attributed.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")
        merge_sha = _commit_file(repo, "feat.txt", "feature\n", "merge PR #42")

        prs = [
            {
                "number": 42,
                "mergeCommit": {"oid": merge_sha},
                "mergedAt": "2026-08-28T12:00:00Z",
                "mergedBy": {"login": "bot"},
            },
        ]
        # `gh repo view` answers "test-org/test-repo" for this checkout.
        config_file = self._write_config(tmp_path, prs, repo="test-org/test-repo")
        self._write_stub_gh(tmp_path)
        env = self._make_env(tmp_path, config_file)

        # Same OID, different repo -- must NOT count as attribution here.
        (tmp_path / "audit.jsonl").write_text(
            json.dumps({
                "actor": "bot", "repo": "other-org/other-repo", "pr": 42,
                "sha": merge_sha, "merged_by": "bot",
                "timestamp": "2026-08-28T12:00:00Z", "script": "gate_merge.sh",
            }) + "\n",
            encoding="utf-8",
        )

        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")

        assert result.returncode == 1, result.stdout + result.stderr
        assert "42" in result.stdout
        assert merge_sha[:12] in result.stdout

    # ---- review fold: the merge OID lookup must not degrade to "unknown" ----

    def test_gate_fails_loudly_when_merge_oid_cannot_be_read(self, tmp_path: Path) -> None:
        """A failed post-merge OID lookup must be loud, not logged as "unknown".

        `gh pr view` can fail after a successful merge. Writing
        `"sha":"unknown"` and exiting 0 tells the operator the merge was
        audited when it was not: the checker matches on the real OID, so that
        merge is reported as unattributed with nothing pointing at the cause.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")
        merge_sha = _commit_file(repo, "feat.txt", "feature\n", "merge PR #42")

        # A stub gh whose merge succeeds but whose mergeCommit lookup fails.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        gh_path = bin_dir / "gh"
        gh_path.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *mergeCommit*) echo 'gh: could not resolve PR' >&2; exit 1;;\n"
            "  *mergedBy*) echo bot;;\n"
            "  *nameWithOwner*) echo test-org/test-repo;;\n"
            "  *number*) echo 42;;\n"
            "  *) exit 0;;\n"
            "esac\n",
            encoding="utf-8",
        )
        gh_path.chmod(0o755)

        config_file = self._write_config(tmp_path, [{
            "number": 42,
            "mergeCommit": {"oid": merge_sha},
            "mergedAt": "2026-08-28T12:00:00Z",
            "mergedBy": {"login": "bot"},
        }])
        audit_file = tmp_path / "audit.jsonl"
        audit_file.write_text("", encoding="utf-8")
        env = self._make_env(tmp_path, config_file, extra={"FLEET_AUDIT_LOG": str(audit_file)})

        gate_result = subprocess.run(
            ["bash", GATE, "42", "tsk-test", "test note"],
            capture_output=True, text=True, check=False, env=env,
        )

        assert gate_result.returncode != 0, gate_result.stdout + gate_result.stderr
        assert "42" in gate_result.stderr
        assert "mergeCommit" in gate_result.stderr
        # _read_field used to discard gh's own stderr (2>/dev/null), so the
        # operator saw only "returned no OID" with the actual cause -- auth
        # failure, rate-limit, missing PR -- thrown away. The stub's stderr
        # is the underlying reason; it must reach the operator too.
        assert "gh: could not resolve PR" in gate_result.stderr

        # Whatever was written must not be able to stand in as attribution:
        # `sha` is the reconciliation key, so it must be empty rather than a
        # placeholder like "unknown" that reads as a recorded value.
        for line in audit_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert json.loads(line).get("sha") == ""

        # The checker still reports the merge -- the gate's nonzero exit is
        # the early warning, not a substitute for the audit going red. Restore
        # the healthy stub first: the failure being simulated is a transient
        # `gh pr view` at merge time, not a permanently broken gh.
        self._write_stub_gh(tmp_path)
        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "42" in result.stdout

    def test_gate_fails_loudly_when_neither_jq_nor_python3_available(self, tmp_path: Path) -> None:
        """The documented "neither jq nor python3" path must actually run.

        `set -e` is restored (line 84) before the audit-encoding block. With
        jq absent, the fallback is a bare `VAR=$(python3 -c ...)` assignment;
        if python3 is ALSO absent, that assignment's own nonzero exit (127,
        "command not found") kills the script under `set -e` before it ever
        reaches its own "neither jq nor python3 is available" handling further
        down. The merge still succeeded, so a raw shell crash here is wrong on
        two counts: exit 65 (audit incomplete) is documented for exactly this
        case, and the operator gets a Python traceback-less shell error
        instead of the message that names the cause.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")
        merge_sha = _commit_file(repo, "feat.txt", "feature\n", "merge PR #43")

        # A pure-/bin/sh gh stub: the usual _write_stub_gh is itself a python3
        # script, which would defeat a PATH with no python3 on it before the
        # script under test ever gets to run. Every lookup this test needs
        # succeeds; the audit-encoding step is the only thing meant to fail.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        gh_path = bin_dir / "gh"
        gh_path.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            f"  *mergeCommit*) echo '{merge_sha}';;\n"
            "  *mergedBy*) echo bot;;\n"
            "  *nameWithOwner*) echo test-org/test-repo;;\n"
            "  *number*) echo 43;;\n"
            "  *) exit 0;;\n"
            "esac\n",
            encoding="utf-8",
        )
        gh_path.chmod(0o755)

        config_file = self._write_config(tmp_path, [{
            "number": 43,
            "mergeCommit": {"oid": merge_sha},
            "mergedAt": "2026-08-28T12:00:00Z",
            "mergedBy": {"login": "bot"},
        }])
        audit_file = tmp_path / "audit.jsonl"
        audit_file.write_text("", encoding="utf-8")
        env = self._make_env(tmp_path, config_file, extra={"FLEET_AUDIT_LOG": str(audit_file)})

        # Rebuild PATH from scratch with only what the script needs to run at
        # all (bash builtins plus basename/dirname/mkdir/date/git/sh, and the
        # gh stub) -- deliberately no jq, no python3, wherever they really
        # live on this host.
        bare_bin = tmp_path / "bare-bin"
        bare_bin.mkdir()
        for tool in ("basename", "dirname", "mkdir", "date", "git", "cat", "sh", "mktemp", "head", "rm"):
            for candidate in (Path("/usr/bin") / tool, Path("/bin") / tool):
                if candidate.exists():
                    (bare_bin / tool).symlink_to(candidate)
                    break
        (bare_bin / "gh").symlink_to(gh_path)
        bash_path = shutil.which("bash") or "/usr/bin/bash"
        env["PATH"] = str(bare_bin)

        gate_result = subprocess.run(
            [bash_path, GATE, "43", "tsk-test", "test note"],
            capture_output=True, text=True, check=False, env=env,
        )

        assert gate_result.returncode == 65, gate_result.stdout + gate_result.stderr
        assert "neither jq nor python3" in gate_result.stderr, gate_result.stderr
        assert "43" in gate_result.stderr

    # ---- review fold: an audit entry with sha but no repo warns, not silence ----

    def test_audit_entry_missing_repo_field_warns_not_just_drops(self, tmp_path: Path) -> None:
        """A `sha` present but `repo` missing/empty must warn, not vanish quietly.

        `e.get("repo") == repo_slug` is False for a missing OR an empty `repo`
        the same way it is for a genuine other-repo entry, so the comprehension
        correctly excludes it from `audit_shas` either way -- the merge is
        still reported as unmatched. But unlike a genuine other-repo entry
        (normal, multi-repo audit log), a present `sha` with no `repo` at all
        is a producer bug or schema drift, and the operator gets nothing
        pointing at that cause: the merge just reads as flatly unattributed.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")
        merge_sha = _commit_file(repo, "feat.txt", "feature\n", "merge PR #42")

        prs = [
            {
                "number": 42,
                "mergeCommit": {"oid": merge_sha},
                "mergedAt": "2026-08-28T12:00:00Z",
                "mergedBy": {"login": "bot"},
            },
        ]
        config_file = self._write_config(tmp_path, prs, repo="test-org/test-repo")
        self._write_stub_gh(tmp_path)
        env = self._make_env(tmp_path, config_file)

        (tmp_path / "audit.jsonl").write_text(
            json.dumps({
                "actor": "bot", "repo": "", "pr": 42,
                "sha": merge_sha, "merged_by": "bot",
                "timestamp": "2026-08-28T12:00:00Z", "script": "gate_merge.sh",
            }) + "\n",
            encoding="utf-8",
        )

        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")

        # Still correctly unmatched -- this must not become a false pass.
        assert result.returncode == 1, result.stdout + result.stderr
        assert "42" in result.stdout
        # ...but now the operator is told why: a real audit line names this
        # sha with no repo attached, not just silence about it.
        assert "WARN" in result.stderr, result.stderr
        assert merge_sha[:12] in result.stderr, result.stderr

    # ---- review fold: a truncated `gh pr list` must not pass as clean ----

    def test_pr_list_at_the_limit_ceiling_is_an_error_not_a_clean_pass(
        self, tmp_path: Path
    ) -> None:
        """`gh pr list --limit 1000` silently caps at 1000 with no truncation
        flag. If the real result has more merges than that in scope, the
        extras never enter the reconciliation set and an unattributed merge
        among them is never reported -- a truncated fetch must not read as a
        clean audit.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")

        prs = [
            {
                "number": n,
                "mergeCommit": {"oid": f"{n:040x}"},
                "mergedAt": "2026-08-28T12:00:00Z",
                "mergedBy": {"login": "bot"},
            }
            for n in range(1, 1001)
        ]
        config_file = self._write_config(tmp_path, prs, repo="test-org/test-repo")
        self._write_stub_gh(tmp_path)
        env = self._make_env(tmp_path, config_file)
        (tmp_path / "audit.jsonl").write_text("", encoding="utf-8")

        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")

        assert result.returncode == 2, result.stdout + result.stderr
        assert "1000" in result.stderr, result.stderr

    # ---- review fold: infrastructure errors must map to EXIT_ERROR too ----

    def test_missing_gh_binary_maps_to_exit_error_not_a_traceback(self, tmp_path: Path) -> None:
        """A `gh` that isn't even on PATH must map to EXIT_ERROR, not crash.

        `subprocess.run(["gh", ...])` raises `FileNotFoundError` (an `OSError`)
        when `gh` is absent, and `main()` only catches `RuntimeError` -- the
        exception escapes, Python exits 1, and an infrastructure failure
        reads as EXIT_FAIL ("one or more merges lack audit entries") instead
        of EXIT_ERROR.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(repo, "README.md", "# main\n", "initial")

        env = os.environ.copy()
        env["PATH"] = str(tmp_path / "empty-bin")  # gh is nowhere on this PATH
        (tmp_path / "empty-bin").mkdir()
        env["HOME"] = str(tmp_path / "home")
        (tmp_path / "audit.jsonl").write_text("", encoding="utf-8")

        result = self._run_checker(tmp_path, env, cutoff="2026-08-28T00:00:00Z")

        assert result.returncode == 2, result.stdout + result.stderr
        assert "Traceback" not in result.stderr, result.stderr
