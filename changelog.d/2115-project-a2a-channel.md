### Fixed

- Projects: creating a project now always creates its A2A channel. Previously only `POST /api/projects` did; the agent skill (`create_project`) and the `project_create` decision approval path created the project row without a channel, leaving members with no project thread (#2115).
