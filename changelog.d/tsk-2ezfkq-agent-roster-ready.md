### Fixed

- Add Array.isArray validation in fetchAgentLists to prevent invalid data from setting agentRosterReady
- Add agentRosterReady prop to ChannelSidebar and use it to control when People chip is shown
- Update existing tests to pass agentRosterReady: true when they expect a People chip
- Fix a failed archived-agents fetch leaving live agent roster empty, breaking author resolution and mentions
- Fix fetchAgentLists to load live and archived agent rosters independently so one failing request never empties the other
- Commit the live agent roster as soon as its own response is valid, so a slow or hung archived request no longer delays it, and tolerate responses with no headers
