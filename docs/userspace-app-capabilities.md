# Userspace App Capability Reference

This document describes every capability a userspace app can call through the
capability broker (`tinyagentos/userspace/broker.py`). App authors use this as
the authoritative reference for what is available, what arguments each call
expects, and what it returns.

## Free vs. Gated Model

Capabilities are split into two tiers:

**Free capabilities** are available to every app without any consent prompt.
These cover data storage, file access, notifications, and window operations.

**Gated capabilities** require the user to explicitly grant permission to the
app before they can be called. If a gated capability is invoked without
granted permission, the broker returns `{"error": "permission_denied"}`.

| Tier   | Namespaces |
|--------|------------|
| Free   | `app.kv`, `app.table`, `app.files`, `app.notify`, `app.window` |
| Gated  | `app.net`, `app.agent`, `app.llm`, `app.memory` |

## Per-App Data Scoping

All data capabilities (`app.kv`, `app.table`) are namespaced by `app_id`. An
app can only read and write its own key-value pairs and table rows; there is
no cross-app data access.

File capabilities (`app.files.read`, `app.files.write`) are jailed to the
app's own `files/` directory under its app directory. Paths that escape this
root (via `..` or symlinks) are rejected with `{"error": "invalid_path"}`.

## Error Shape

Every capability call returns one of two shapes:

```json
{"result": <value>}
{"error": "<code>", ...}
```

Possible error codes:

| Code                  | Meaning                                     |
|-----------------------|---------------------------------------------|
| `unknown_capability`  | The capability string is not recognized.    |
| `permission_denied`   | A gated capability was called without grant.|
| `missing_arg`         | A required argument was not provided.       |
| `invalid_path`        | A file path escaped the jail root.          |
| `not_found`           | A file read target does not exist.          |
| `no_backend`          | `app.net` called but no backend URL is set. |
| `backend_unreachable` | The backend proxy request failed.           |

---

## Free Capabilities

### app.kv.get

Read a value from the app's key-space store.

**Args:**

| Arg | Required | Type   | Description     |
|-----|----------|--------|-----------------|
| key | yes      | string | Key to look up. |

**Returns:** `{"result": <value>}` (the stored value, or `None` if missing).

---

### app.kv.set

Write a value to the app's key-space store.

**Args:**

| Arg   | Required | Type   | Description       |
|-------|----------|--------|-------------------|
| key   | yes      | string | Key to write.     |
| value | no       | any    | Value to store.   |

**Returns:** `{"result": true}`

---

### app.kv.delete

Delete a key from the app's key-space store.

**Args:**

| Arg | Required | Type   | Description       |
|-----|----------|--------|-------------------|
| key | yes      | string | Key to delete.    |

**Returns:** `{"result": true}`

---

### app.kv.keys

List all keys in the app's key-space store.

**Args:** none

**Returns:** `{"result": [<string>, ...]}`

---

### app.table.insert

Insert a row into a table.

**Args:**

| Arg  | Required | Type   | Description                         |
|------|----------|--------|-------------------------------------|
| table| yes      | string | Table name.                         |
| row  | no       | object | Row data (defaults to `{}` if absent). |

**Returns:** `{"result": <insert_result>}`

---

### app.table.query

Query rows from a table.

**Args:**

| Arg  | Required | Type   | Description                         |
|------|----------|--------|-------------------------------------|
| table| yes      | string | Table name.                         |
| where| no       | any    | Filter condition (optional).        |

**Returns:** `{"result": [<row>, ...]}`

---

### app.table.delete

Delete a row from a table by id.

**Args:**

| Arg  | Required | Type   | Description       |
|------|----------|--------|-------------------|
| table| yes      | string | Table name.       |
| id   | yes      | any    | Row id to delete. |

**Returns:** `{"result": true}`

---

### app.files.read

Read a file from the app's `files/` directory.

**Args:**

| Arg | Required | Type   | Description                         |
|-----|----------|--------|-------------------------------------|
| path| no       | string | Relative path within `files/` root. |

**Returns:** `{"result": <string>}` (file contents).

**Errors:** `invalid_path` if the resolved path escapes the jail root;
`not_found` if the file does not exist.

---

### app.files.write

Write a file into the app's `files/` directory. Parent directories are created
as needed.

**Args:**

| Arg    | Required | Type   | Description                         |
|--------|----------|--------|-------------------------------------|
| path   | no       | string | Relative path within `files/` root. |
| content| no       | string | Text content to write.              |

**Returns:** `{"result": true}`

**Errors:** `invalid_path` if the path resolves to the jail root itself or to
an existing directory.

---

### app.notify

Send a notification through the platform notification service.

**Args:**

| Arg  | Required | Type   | Description              |
|------|----------|--------|--------------------------|
| title| no       | string | Notification title.      |
| body | no       | string | Notification body text.  |

**Returns:** `{"result": true}`

The notification is dispatched with level `"info"` and icon `"layout-grid"`.
If no notification service is configured, the call is silently ignored.

---

### app.window

Window operations. This is a server-side no-op; window management is handled
entirely on the client.

**Args:** none

**Returns:** `{"result": true}`

---

## Gated Capabilities

These capabilities require the user to grant the corresponding namespace
permission before they can be called.

### app.memory.search

Search the platform memory service.

**Args:**

| Arg | Required | Type   | Description                         |
|-----|----------|--------|-------------------------------------|
| q   | no       | string | Search query (defaults to `""`).    |

**Returns:** `{"result": [...]}` (list of search results, or `[]` if the
memory service is unavailable or the search fails).

---

### app.agent

Send a message to a named agent.

**Args:**

| Arg    | Required | Type   | Description          |
|--------|----------|--------|----------------------|
| name   | no       | string | Agent name.          |
| message| no       | string | Message to send.     |

**Returns:** `{"result": <agent_response>}` or `{"result": null}` if no agent
service is configured.

---

### app.llm

Send a completion request to the platform LLM service.

**Args:**

| Arg   | Required | Type   | Description                    |
|-------|----------|--------|--------------------------------|
| prompt| no       | string | Completion prompt (defaults to `""`). |

**Returns:** `{"result": <completion>}` or `{"result": null}` if no LLM
service is configured.

---

### app.net

Proxy an HTTP request to the app's configured backend URL. This is the only
gated capability that makes outbound network requests on behalf of the app.

**Args:**

| Arg    | Required | Type   | Description                                  |
|--------|----------|--------|----------------------------------------------|
| path   | no       | string | Backend path (appended to backend base URL). |
| method | no       | string | HTTP method (defaults to `"GET"`).           |
| body   | no       | any    | JSON request body.                           |
| headers| no       | object | Request headers (blocked headers are stripped). |

**Returns:** `{"result": {"status": <int>, "body": <string>}}`

**Errors:** `no_backend` if no backend URL is configured;
`backend_unreachable` if the request fails; `invalid_path` if the path
contains a protocol, starts with `//`, or contains `..` segments.

**Blocked headers:** The following headers are stripped from every request to
prevent identity spoofing and session exfiltration: `host`, `authorization`,
`cookie`, `x-forwarded-for`, `x-forwarded-host`, `x-forwarded-proto`.

**SSRF protection for app installs:** When installing an app from a remote
`.taosapp` package, the install URL is validated against private, loopback,
link-local, reserved, unspecified, and multicast IP ranges. Only `http` and
`https` URLs with fully public resolved addresses are allowed. See
`tinyagentos/userspace/url_guard.py` for the implementation.
