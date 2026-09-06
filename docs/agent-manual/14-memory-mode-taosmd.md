# Memory Mode: taosmd

<!-- Guide for agents deployed with memory_mode=taosmd (taOSmd only). -->

## What this mode means

All memory goes to taOSmd. The framework's native memory is not used. This mode
is for frameworks with no native memory, or for users who want one durable store
for everything.

## When to use it

- The framework has no native memory system.
- The user wants all memory searchable and shared across the fleet.
- The agent runs on volatile infrastructure and needs memory to survive
  redeploys without any local scratchpad.

## How to behave

- Write everything to taOSmd. Do not attempt to use framework memory.
- Read from taOSmd at session start to load context.
- taOSmd is the single source of truth. There is no second store to conflict
  with.

## What NOT to do

- Do not try to use framework memory. It may not exist or may not persist.
- Do not write to a local file as a workaround. taOSmd is the store.
- Do not cache large working state in your context window as a substitute for
  memory. Summarise and store to taOSmd instead.
