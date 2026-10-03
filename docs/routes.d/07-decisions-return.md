# What `GET /api/decisions/agent` returns (grant scoping)

<!-- Route module `tinyagentos/routes/decisions.py`, scope `decisions_write`. Lists decisions THIS agent raised; store enforces `from_agent` binding, no cross-agent leakage -->

## Grant shaping which decisions come back

- **Global (null-project) grant**: null-project decisions ONLY
- **Exactly one project grant**: that project's decisions, filtered in store query
- **Two or more projects**: fetched by agent, filtered in Python

### Limit interaction

- Global/single-project: project filter pushed into store query, 500 limit applies AFTER scoping (#2194)
- Two-or-more: fetches up to 500 then filters in Python, so agent with several grants and >500 total decisions can lose allowed-project rows to limit (same shape as original bug, narrower blast radius)