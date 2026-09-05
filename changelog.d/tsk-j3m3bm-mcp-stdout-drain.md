- `mcp`: the supervisor now drains an MCP server's **stdout** as well as its
  stderr. Both pipes were captured but only stderr was ever read, so any server
  writing more than the 64 KiB pipe buffer to stdout blocked in `write()`
  forever while the supervisor kept reporting it healthy — and for a
  stdio-transport server stdout is the JSON-RPC channel, so that was the
  primary data path, not an edge case. Log entries now carry a `stream` field
  (`stdout`/`stderr`), and the Logs tab tails both.
- `mcp`: both drains read the pipes in fixed-size chunks instead of iterating
  lines. `async for line in reader` raises `ValueError` once a single line
  exceeds `StreamReader`'s 64 KiB limit, which killed the drain task and
  re-opened the same deadlock — a JSON-RPC frame is one line and routinely
  larger than that.
- `mcp`: `POST /api/mcp/call` answers `501 not_implemented` while the JSON-RPC
  transport is unwired. It used to answer `200` with
  `{"ok": true, "result": "stub ..."}`, which no caller could tell apart from a
  real tool result.
