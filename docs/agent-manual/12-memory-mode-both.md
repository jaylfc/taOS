# Memory Mode: both

<!-- Guide for agents deployed with memory_mode=both (the default). -->

## Your memory layout

You have two stores running in parallel:

- **Framework memory** — fast, local, lives in the container. Dies on redeploy.
  Use it for the live working set: what the user said this turn, in-progress
  task state, scratchpad reasoning.
- **taOSmd** — durable, cross-agent, semantic, survives redeploy. Use it for
  facts that must outlast this session: identity, preferences, long-term
  knowledge, decisions, and anything the user asks you to remember.

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

At the end of every turn, push durable facts to taOSmd. Do not let them pile
up in framework memory, because framework memory dies on redeploy.

At the start of every session, read durable facts from taOSmd back into your
context. Do not re-ask the user for facts they already told you.

## Conflict rule

If framework memory and taOSmd contradict on a durable fact, taOSmd wins.
Framework memory is authoritative only for live working state. If you read a
conflict, trust taOSmd and update framework memory to match.

## What NOT to do

- Do not write the same fact to both stores on every turn. Write volatile
  content to framework memory only. Write durable content to taOSmd only.
- Do not let framework memory become the long-term store. It is a scratchpad.
- Do not skip the turn-boundary push. A weak model that writes nothing to
  taOSmd until session end is fine. A model that writes everything to
  framework memory breaks the split.
