### Fixed
- `create_container` now refuses to create a local container if a container with the same name already exists in any Incus project, returning an error that names the container and the project holding it.
