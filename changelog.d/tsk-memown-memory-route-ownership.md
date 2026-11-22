### Fixed
- Memory routes now require that the caller owns the agent. A member can only browse, search, list and delete the memory of an agent they own; admins and the host token are unchanged, and an agent token is limited to its own agent.
- Memory without an agent now uses your own private index. Members no longer share one; the install owner keeps the existing index.
- Per-agent routes now require that the caller owns the agent (or is an admin): channels, the agent debugger, traces and spans, the recycle bin, skills, broker access requests and grant lists, and library import. An agent's own token reaches only its own agent. Channel and recycle lists show only your own agents. Trace logs of system names such as governance and cluster tasks are for the install owner.
- Approving or denying an agent's access to a provider key, and triggering a framework update, are now limited to the install owner. The agent's owner can still revoke a grant.
