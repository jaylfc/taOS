# Memory Mode: both

<!-- Guide for agents deployed with memory_mode=both (the default). -->

## Your memory layout

You have two stores running in parallel:

- **Framework memory** — fast, local store in the container. Dies on redeploy. Use it for the live working set: user input, task state, scratchpad reasoning.
- **taOSmd** — durable, cross-agent store that survives redeploy. Use it for facts: identity, preferences, long-term knowledge, decisions, and anything the user asks you to remember.

## When to write where

**Write to framework memory when:**
- The user just told you something for this conversation.
- You are tracking a multi-step task in progress.
- The content is ephemeral (draft, scratch, temporary state).

**Write to taOSmd when:**
- The user said "remember this" or equivalent.
- The fact is durable: name, preference, decision, learned fact.
- Another agent might need this fact.
- You are ending a session and want the fact to survive redeploy.

## The turn boundary rule

At the end of every turn, move durable facts to taOSmd: write them there and drop them from framework memory. Do not let them pile up in framework memory; it dies on redeploy.

At the start of every session, read durable facts from taOSmd back into your context. Do not re-ask the user for facts they already told you.

## Conflict rule

If framework memory and taOSmd contradict on a durable fact, taOSmd wins. Framework memory is authoritative only for live working state. If you read a conflict, trust taOSmd and update framework memory to match.

## What NOT to do

- Do not keep the same fact in both stores. Volatile content lives in framework memory only. Durable content lives in taOSmd only.
- Do not let framework memory become the long-term store. It is a scratchpad.
- Do not skip the turn-boundary move. Write durable facts to taOSmd at the end of every turn, not at session end: a redeploy can come first. A model that writes everything to framework memory breaks the split.
