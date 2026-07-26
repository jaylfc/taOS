# Apps

<!-- One-line description of every taOS app. -->

## The apps (one line each)

- **Messages**: the main chat. Talk to one agent (DM), several (group), or topic channels.
- **Agents**: deploy or import agents (e.g. Hermes), configure, start, stop. Pick framework, model, and base images.
- **Projects**: kanban boards and docs; agents can join a project's channel.
- **Files**: browse agent workspaces, user workspace, shared folders. Upload and download.
- **Store**: one-click install of community apps. Each app gets its own container and a safe port.
- **Models**: see and pull local models; pin cloud models.
- **Providers**: add cloud API keys (OpenAI, Anthropic, and compatible).
- **Cluster**: pair other machines into the compute mesh with a six-digit code.
- **Memory**: browse and manage what agents remember.
- **Settings**: theme, providers, backends, updates, backups, container runtime.
- **Activity**: live feed of everything agents do (tool calls, model calls, errors).
- **Decisions**: your inbox for agent approvals and questions.
- **Observatory**: watch the agent fleet; pause or throttle work lanes.
- **Project Files**: read and write files in projects via REST API.

## Project Files

Agents can read and write project files using REST APIs with project-level permissions:

- `GET /api/projects/{slug}/files?path=<subdir>` - list files (requires `files_read`)
- `GET /api/projects/{slug}/files/{path}` - download a file (requires `files_read`)
- `POST /api/projects/{slug}/files/upload?path=<subdir>` - upload files, multipart field `file` (requires `files_write`)
- `POST /api/projects/{slug}/mkdir` with `{"path": "<subdir>"}` (requires `files_write`)

These routes use the project SLUG in the path, not the internal project ID. Agents authenticate with their registry JWT (`Authorization: Bearer <token>`). Access is project-specific; tokens for different projects return 404.

Other bundled apps (Library, Channels, Secrets, Tasks, Images, MCP, Guides and more); if you do not know one, guess from its name and point to Guides.
