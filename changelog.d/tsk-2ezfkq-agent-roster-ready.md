### Fixed

- Add Array.isArray validation in fetchAgentLists to prevent invalid data from setting agentRosterReady
- Add agentRosterReady prop to ChannelSidebar and use it to control when People chip is shown
- Update existing tests to pass agentRosterReady: true when they expect a People chip
- Fix a failed archived-agents fetch leaving live agent roster empty, breaking author resolution and mentions
- Fix fetchAgentLists to load live and archived agent rosters independently so one failing request never empties the other
- Replace allSettled with two independent async loaders in fetchAgentLists to prevent slow/archived requests from delaying live agent roster; each loader wraps its body in try/catch, sets live/archived agents as soon as valid, uses Promise.all, and handles headerless responses with res.headers?.get("content-type") ?? ""