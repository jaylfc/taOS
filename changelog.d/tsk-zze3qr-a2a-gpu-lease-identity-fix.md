### Fixed

- A2A GPU lease: the admin path in `_resolve_actor` no longer reads the request body's `holder=` field as the acting identity. `holder` is display text only; ownership is an identity match against the fixed `@operator` principal, so an operator cannot satisfy `_lease_owned_by` on an agent's `a2a:` lease by merely setting `holder` in the body. The explicit `lease_id` path and `_may_act_on` remain the sanctioned operator override.
