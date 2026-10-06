### Fixed
- Chat routes now bind the message author to the presenting agent's local token (`request.state.agent_name`), so a deployed agent cannot post, react, type, think, or stream as another agent or as a human.
