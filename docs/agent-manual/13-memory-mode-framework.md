# Memory Mode: framework

<!-- Guide for agents deployed with memory_mode=framework (native memory only). -->

## What this mode means

All memory stays in your framework's native store. Nothing is sent to taOSmd.
This is the fastest option: no network call, no semantic index, no cross-agent
share.

## When to use it

- The user wants maximum speed and zero network dependency for memory.
- The agent's working set is small and fits comfortably in the framework store.
- The user does not need cross-agent memory sharing or semantic search.

## How to behave

- Store everything in framework memory. Do not call any taOSmd memory endpoint.
- On redeploy, all memory is lost. Tell the user this when they first enable
  the mode.
- If the user asks you to remember something long-term, warn them that it will
  not survive a container restart in this mode, and suggest switching to `both`
  or `taosmd` instead.

## What NOT to do

- Do not call taOSmd memory APIs. In this mode they are disabled by design.
- Do not pretend memory survives redeploy. Be honest about the limitation.
- Do not silently fall back to taOSmd. If the framework store fails, report the
  error. Do not route writes to taOSmd as a workaround.
