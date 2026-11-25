"""Structured backup/export bundle builder (#620, slice 1: export only).

``POST /api/backup`` already ships a raw tarball of data-dir files --
``config.yaml`` included verbatim, secrets and all. This module builds the
other thing issue #620 asks for: a JSON bundle of the install's
configuration and data that is safe to keep, share and (in a later slice)
import, because secret values never appear in it.

Secret handling, in order of strength:

1. **Secrets-store values are never read for export.** The bundle carries
   secret *references* -- name, category, description, agent grants from
   ``SecretsStore.list()``, which never selects the value column -- so a
   future import can prompt for re-entry. The values themselves are fetched
   only to scrub the rest of the bundle (rule 3), never written to it.
2. **Secret-bearing keys are replaced wholesale.** Any mapping key whose
   words match the sensitive-key list in ``tinyagentos.log_redaction``
   (the codebase's canonical list: ``api_key``, ``token``, ``secret``,
   ``password``, ...) has its value replaced with the same module's
   ``[REDACTED]`` placeholder, at any depth. Matching is whole-word, like
   log_redaction, so ``monkey`` does not trip ``key``.
3. **String values are scrubbed.** A string containing a literal
   secrets-store value (length >= 6, log_redaction's floor) is replaced
   wholesale; otherwise credential shapes are masked in place -- PEM
   blocks, connection-string passwords, and bare provider-key/JWT shapes
   (log_redaction's patterns). Log redaction's LAN-IP and invite-URL rules
   are deliberately NOT applied: an export that masks backend addresses
   cannot be restored from.

4. **Webhook URLs are credentials.** A ``url`` under a ``webhooks``
   section is replaced wholesale, whatever its shape, and Discord/Slack
   webhook URLs are masked wherever they appear in a string (the host
   stays readable; the credential path does not). The export key list
   also extends log_redaction's with the whole words ``key``, ``pin``,
   ``hash`` and ``salt`` -- a file that leaves the box is matched more
   strictly than log output; whole-word tokenisation keeps
   ``keyboard_layout`` and ``monkey_name`` safe.
5. **Absolute paths do not leave the box.** Values under ``taosmd_dir``
   and ``workspace`` keys are rewritten relative to the data dir when
   they live under it, and reduced to their final component otherwise.

The manifest flags ``contains_all_users_data`` -- the bundle holds every
account's preferences, not just the exporting admin's -- and the
serialized bundle is size-capped (``MAX_EXPORT_BYTES``), so runaway
growth fails with a clear error instead of an unbounded response.

Every redaction and every deliberate omission is recorded in the bundle's
``manifest.excluded`` list, so nothing vanishes silently. The manifest
also carries the format name/version and export time so a future import
slice can validate a bundle before applying it.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyagentos import __version__
from tinyagentos.log_redaction import (
    _CONN_STR_RE,
    _PEM_RE,
    _SENSITIVE_KEYS,
    _TOKEN_SHAPE_RE,
    PLACEHOLDER,
)

FORMAT = "taos-backup-export"
FORMAT_VERSION = 1

_SENSITIVE_SET = frozenset(k.lower() for k in _SENSITIVE_KEYS)

# Export is stricter than log redaction: the file leaves the box, so the
# whole words ``key``, ``pin``, ``hash`` and ``salt`` are sensitive here
# too (FOO_KEY, MY_PIN, pin_hash, *_salt -- env-style SCREAMING_SNAKE
# names tokenise exactly like snake_case ones). log_redaction's own list
# is deliberately NOT widened: it drives log masking repo-wide. Matching
# stays whole-word (see _key_tokens), so substring lookalikes such as
# ``keyboard_layout``, ``monkey_name`` and ``pinned_view`` are untouched.
_EXTRA_SENSITIVE_WORDS = frozenset({"key", "pin", "hash", "salt"})
_EFFECTIVE_SENSITIVE_SET = _SENSITIVE_SET | _EXTRA_SENSITIVE_WORDS

# Discord/Slack webhook URLs embed their credential in the path; mask
# the path and keep the host, the same readability trade as
# connection-string passwords. The structural half of the webhook rule
# (any ``url`` under a ``webhooks`` section, whatever the host) lives in
# sanitize_for_export.
_WEBHOOK_URL_RE = re.compile(
    r"(?P<pre>https://(?:discord\.com|discordapp\.com)/api/webhooks/"
    r"|https://hooks\.slack\.com/services/)(?P<secret>\S+)"
)

# Keys whose values are host filesystem paths. Absolute values leak the
# host layout (usernames, mount points) into a file meant to be kept and
# shared, so they are rewritten relative to the data dir or stripped.
_PATH_KEYS = frozenset({"taosmd_dir", "workspace"})
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

# The bundle is assembled in memory; cap the serialized size so a
# pathological install fails with a clear error instead of an unbounded
# response. Realistic bundles (config + prefs + projects) are kilobytes
# to low megabytes, so this only trips on runaway growth.
MAX_EXPORT_BYTES = 64 * 1024 * 1024


class ExportTooLargeError(ValueError):
    """The serialized export bundle exceeds MAX_EXPORT_BYTES."""

# Mirrors log_redaction's floor: a shorter known value is too likely a
# common substring to mask on.
_MIN_KNOWN_VALUE_LEN = 6

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD_RE = re.compile(r"[^A-Za-z0-9]+")

# Agent-registry fields safe to export: identity and shape, never key
# material (the registry table holds none, and this whitelist keeps it
# that way if columns are added).
_REGISTRY_FIELDS = (
    "canonical_id", "display_name", "handle", "framework", "origin",
    "role", "status", "user_id", "capabilities", "created_ts", "revoked_at",
)


def _key_tokens(key: str) -> list[str]:
    tokens: list[str] = []
    for part in _NON_WORD_RE.split(key):
        tokens.extend(t.lower() for t in _CAMEL_RE.split(part) if t)
    return tokens


def _is_secret_key(key: str) -> bool:
    if key.lower() in _EFFECTIVE_SENSITIVE_SET:
        return True
    tokens = _key_tokens(key)
    if any(t in _EFFECTIVE_SENSITIVE_SET for t in tokens):
        return True
    return any(
        f"{a}_{b}" in _EFFECTIVE_SENSITIVE_SET
        for a, b in zip(tokens, tokens[1:])
    )


def _sanitize_string(
    text: str,
    path: str,
    redactions: list[dict],
    known_values: list[str],
) -> str:
    for val in known_values:
        if len(val) >= _MIN_KNOWN_VALUE_LEN and val in text:
            redactions.append({
                "path": path,
                "reason": "string contains a value held in the secrets store",
            })
            return PLACEHOLDER
    cleaned = _PEM_RE.sub(
        lambda m: m.group("begin") + PLACEHOLDER + m.group("end"), text
    )
    cleaned = _CONN_STR_RE.sub(
        lambda m: m.group("pre") + PLACEHOLDER + m.group("post"), cleaned
    )
    cleaned = _WEBHOOK_URL_RE.sub(
        lambda m: m.group("pre") + PLACEHOLDER, cleaned
    )
    cleaned = _TOKEN_SHAPE_RE.sub(PLACEHOLDER, cleaned)
    if cleaned != text:
        redactions.append({
            "path": path,
            "reason": "credential-shaped content masked",
        })
    return cleaned


def _rewrite_path(
    value: str,
    path: str,
    redactions: list[dict],
    data_dir: str | None,
) -> str | None:
    """Rewrite an absolute path for export, or None if *value* is not one.

    Under the data dir the path becomes relative to it (meaningful again
    on a fresh install with a different layout); outside it, only the
    final component survives. Either way the rewrite is recorded.
    """
    if not (value.startswith("/") or _DRIVE_RE.match(value)):
        return None
    if data_dir:
        rel = os.path.relpath(value, data_dir)
        if rel != ".." and not rel.startswith(".." + os.sep):
            redactions.append({
                "path": path,
                "reason": "absolute path rewritten relative to the data dir",
            })
            return rel
    redactions.append({
        "path": path,
        "reason": "absolute path outside the data dir; reduced to its "
                  "final component so the host layout is not exported",
    })
    return os.path.basename(value.rstrip("/")) or value


def sanitize_for_export(
    value: Any,
    path: str,
    redactions: list[dict],
    *,
    known_values: list[str] | tuple[str, ...] = (),
    data_dir: str | None = None,
    _in_webhooks: bool = False,
) -> Any:
    """Return a copy of *value* with every secret removed.

    ``path`` is the dotted location used in redaction records
    (``config.backends[0].api_key``). ``redactions`` is appended to, one
    entry per redaction, each ``{"path": ..., "reason": ...}``.
    ``data_dir`` is the install's data directory, used to rewrite the
    absolute paths named in ``_PATH_KEYS`` relative to it.
    """
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            child = f"{path}.{k}"
            if isinstance(k, str) and _is_secret_key(k):
                if v not in (None, "", [], {}):
                    redactions.append({
                        "path": child,
                        "reason": "secret-bearing key; value replaced "
                                  "with the redaction marker",
                    })
                    out[k] = PLACEHOLDER
                else:
                    out[k] = v
                continue
            if _in_webhooks and isinstance(k, str) and k.lower() == "url":
                # A webhook URL is itself a credential (Discord/Slack
                # tokens live in the path; generic endpoints embed
                # theirs unpredictably), so it is replaced wholesale
                # whatever its shape.
                if v not in (None, "", [], {}):
                    redactions.append({
                        "path": child,
                        "reason": "webhook URL; webhook URLs are "
                                  "credentials and are never exported",
                    })
                    out[k] = PLACEHOLDER
                else:
                    out[k] = v
                continue
            if (
                isinstance(k, str)
                and k.lower() in _PATH_KEYS
                and isinstance(v, str)
            ):
                rewritten = _rewrite_path(v, child, redactions, data_dir)
                if rewritten is not None:
                    out[k] = rewritten
                    continue
            out[k] = sanitize_for_export(
                v, child, redactions, known_values=known_values,
                data_dir=data_dir,
                _in_webhooks=_in_webhooks
                or (isinstance(k, str) and k.lower() == "webhooks"),
            )
        return out
    if isinstance(value, (list, tuple)):
        return [
            sanitize_for_export(
                v, f"{path}[{i}]", redactions, known_values=known_values,
                data_dir=data_dir, _in_webhooks=_in_webhooks,
            )
            for i, v in enumerate(value)
        ]
    if isinstance(value, str):
        return _sanitize_string(value, path, redactions, list(known_values))
    return value


def serialize_export_bundle(bundle: dict) -> str:
    """Serialize *bundle*, refusing anything over MAX_EXPORT_BYTES.

    The bundle is built whole in memory; the cap turns runaway growth
    into a clear error at the route instead of an unbounded response.
    """
    raw = json.dumps(bundle)
    size = len(raw.encode("utf-8"))
    if size > MAX_EXPORT_BYTES:
        raise ExportTooLargeError(
            f"export bundle is {size} bytes, over the "
            f"{MAX_EXPORT_BYTES}-byte cap"
        )
    return raw


async def build_export_bundle(
    *,
    config,
    notifications=None,
    projects=None,
    desktop_settings=None,
    secrets=None,
    agent_registry=None,
    now: datetime | None = None,
) -> dict:
    """Assemble the export bundle from the install's live stores.

    Every store argument is optional: a missing store omits its section
    and records the omission in ``manifest.excluded`` rather than failing
    the whole export.
    """
    redactions: list[dict] = []
    omissions: list[dict] = []

    # Secret references first: the known values scrub every section below.
    secret_references: list[dict] = []
    known_values: list[str] = []
    if secrets is not None:
        for meta in await secrets.list():
            secret_references.append({
                k: meta.get(k)
                for k in ("name", "category", "description", "write_once", "agents")
            })
            record = await secrets.get(meta["name"])
            if record and record.get("value"):
                known_values.append(record["value"])
        omissions.append({
            "path": "secrets",
            "reason": "secret values are never exported; names, categories "
                      "and agent grants are listed under secret_references "
                      "so an import can prompt for re-entry",
        })
    else:
        omissions.append({
            "path": "secrets",
            "reason": "secrets store unavailable on this install",
        })

    # The data dir is the config file's directory, the codebase-wide
    # convention (routes derive it from config_path the same way).
    config_path = getattr(config, "config_path", None)
    data_dir = str(Path(config_path).parent) if config_path else None

    def clean(value: Any, path: str) -> Any:
        return sanitize_for_export(
            value, path, redactions, known_values=known_values,
            data_dir=data_dir,
        )

    bundle: dict[str, Any] = {}

    # Config, with the agent lists lifted into their own section.
    config_dict = config.to_dict()
    config_agents = config_dict.pop("agents", [])
    config_archived = config_dict.pop("archived_agents", [])
    if getattr(config, "wallhaven_api_key", None):
        # A secret config field AppConfig.to_dict() already omits; record
        # the omission so the manifest is complete.
        omissions.append({
            "path": "config.wallhaven_api_key",
            "reason": "secret config field; not exported",
        })
    bundle["config"] = clean(config_dict, "config")

    # Agents: config-defined agents plus registry identities.
    agents_section: dict[str, Any] = {
        "active": clean(config_agents, "agents.active"),
        "archived": clean(config_archived, "agents.archived"),
    }
    if agent_registry is not None:
        rows = await agent_registry.list_all()
        agents_section["registry"] = clean(
            [
                {k: row.get(k) for k in _REGISTRY_FIELDS if k in row}
                for row in rows
            ],
            "agents.registry",
        )
    else:
        omissions.append({
            "path": "agents.registry",
            "reason": "agent registry unavailable on this install",
        })
    bundle["agents"] = agents_section

    # Projects, each with its membership rows.
    if projects is not None:
        project_entries = []
        for project in await projects.list_projects(status=None):
            entry = dict(project)
            entry["members"] = await projects.list_members(project["id"])
            project_entries.append(entry)
        bundle["projects"] = clean(project_entries, "projects")
    else:
        omissions.append({
            "path": "projects",
            "reason": "project store unavailable on this install",
        })

    # Notification delivery prefs + DND per user (no notification content).
    if notifications is not None:
        bundle["notification_prefs"] = clean(
            await notifications.export_prefs(), "notification_prefs"
        )
    else:
        omissions.append({
            "path": "notification_prefs",
            "reason": "notification store unavailable on this install",
        })

    # Desktop settings + namespaced preferences per user.
    if desktop_settings is not None:
        bundle["preferences"] = clean(
            await desktop_settings.export_all(), "preferences"
        )
    else:
        omissions.append({
            "path": "preferences",
            "reason": "desktop settings store unavailable on this install",
        })

    bundle["secret_references"] = secret_references

    exported_at = (now or datetime.now(timezone.utc)).isoformat()
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "exported_at": exported_at,
        "taos_version": __version__,
        "contains_all_users_data": True,
        "sections": [k for k in bundle],
        "excluded": omissions + redactions,
    }
    return {"manifest": manifest, **bundle}
