### Security
- LLM gateway: scoped keys for the in-process gateway at `/api/llm/v1`.
  A key is bound to one agent or one cluster node, is stored only as a hash,
  and can use only the models it names. A key with an empty model list can
  use no model (LiteLLM read an empty list as "every model").
- LLM gateway: a key allowed `taos-default` can use whatever the owner has
  set as the default model, and keeps working when the owner changes it.
  Listing the concrete model does not grant the alias.
- LLM gateway: existing per-agent LiteLLM keys keep working on the gateway,
  and an agent over its LLM budget is refused before its request is sent,
  with the same 429 LiteLLM gave.
- Revoking, blocking or deleting a cluster node now also cuts that node's
  model access in the same step, and archiving an agent revokes its gateway
  keys even when the LiteLLM proxy is not running.
