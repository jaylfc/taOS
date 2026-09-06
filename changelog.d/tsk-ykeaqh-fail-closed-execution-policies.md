### Fixed

- The delegation and skill-exec governance gates now deny (403) when `app.state.execution_policies` is absent instead of silently allowing the call. This covers governed calls only: an admin human session and an unclassified skill each return before the store is consulted, unchanged by this fix. An absent store indicates a misconfigured app because the startup wiring in `app.py` sets `app.state.execution_policies` unconditionally.
