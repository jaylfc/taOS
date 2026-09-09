### Fixed
- memory_mode 'framework' is now enforced: tm_agents.register_agent is skipped
  and AGENTS.md taosmd rules are not spliced when memory_mode='framework'.
- Deploy wizard couples memoryMode to memoryPlugin and hides the Memory Layer
  controls when framework-only mode is selected.
