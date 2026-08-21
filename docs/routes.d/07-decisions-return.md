# What `GET /api/decisions/agent` returns (grant scoping)

<!-- Route module `tinyagentos/routes/decisions.py`, scope `decisions_write`. Lists the decisions THIS agent raised; the store layer enforces the `from_agent` binding, so there is no cross-agent leakage regardless of grants -->

## Grant shaping which decisions come back

### Global (null-project) grant

- **null-project decisions ONLY**

### Exactly one project grant

- That project's decisions, filtered in the store query

### Two or more projects

- Fetched by agent, then filtered in Python

### Limit interaction

- The global and single-project paths push the project filter into the store query, so the 500 limit applies AFTER scoping (issue #2194)
- The two-or-more-project path still fetches up to 500 rows for the agent and filters afterwards in Python, so an agent holding grants on several projects and carrying more than 500 decisions in total can still lose allowed-project rows to the limit
- Same shape as the original bug, narrower blast radius