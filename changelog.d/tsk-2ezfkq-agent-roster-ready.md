### Fixed

- Add Array.isArray validation in fetchAgentLists to prevent invalid data from setting agentRosterReady
- Add agentRosterReady prop to ChannelSidebar and use it to control when People chip is shown
- Update existing tests to pass agentRosterReady: true when they expect a People chip