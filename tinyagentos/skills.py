from __future__ import annotations
import json
import time
from tinyagentos.base_store import BaseStore


class SkillStore(BaseStore):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS skills (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT DEFAULT '',
        version TEXT NOT NULL DEFAULT '1.0.0',
        tool_schema TEXT NOT NULL DEFAULT '{}',
        frameworks TEXT NOT NULL DEFAULT '{}',
        requires_services TEXT DEFAULT '[]',
        requires_hardware TEXT DEFAULT '{}',
        install_method TEXT NOT NULL DEFAULT 'builtin',
        install_target TEXT DEFAULT '',
        installed INTEGER DEFAULT 1,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);

    CREATE TABLE IF NOT EXISTS agent_skills (
        agent_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        config TEXT DEFAULT '{}',
        PRIMARY KEY (agent_id, skill_id)
    );
    CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills(agent_id);
    """

    async def _post_init(self):
        # Seed is idempotent (INSERT OR IGNORE on the id PK), so run it on every
        # startup: a fresh install gets the full set, and an EXISTING install
        # backfills any builtin skills added since it was first seeded (e.g. new
        # desktop-control tools) without disturbing user-installed skills.
        await self._seed_defaults()

    async def _seed_defaults(self):
        """Seed the default skill set."""
        defaults = [
            {
                "id": "memory_search",
                "name": "Memory Search",
                "category": "search",
                "description": "Search the agent's knowledge base",
                "tool_schema": {
                    "name": "memory_search",
                    "description": "Search stored documents and memories",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "limit": {"type": "integer", "default": 10},
                        },
                        "required": ["query"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "native", "pocketflow": "adapter",
                    "langroid": "adapter", "openai-agents-sdk": "adapter", "hermes": "adapter",
                    "agent-zero": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.memory_search",
            },
            {
                "id": "file_read",
                "name": "File Read",
                "category": "files",
                "description": "Read files from the agent workspace",
                "tool_schema": {
                    "name": "file_read",
                    "description": "Read a file from the workspace",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative file path"},
                        },
                        "required": ["path"],
                    },
                },
                "frameworks": {
                    "smolagents": "native", "openclaw": "native", "pocketflow": "adapter",
                    "langroid": "adapter", "openai-agents-sdk": "adapter", "hermes": "adapter",
                    "agent-zero": "native", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.file_read",
            },
            {
                "id": "file_write",
                "name": "File Write",
                "category": "files",
                "description": "Write files to the agent workspace",
                "tool_schema": {
                    "name": "file_write",
                    "description": "Write content to a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "native", "pocketflow": "adapter",
                    "langroid": "adapter", "openai-agents-sdk": "adapter", "hermes": "adapter",
                    "agent-zero": "native", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.file_write",
            },
            {
                "id": "list_files",
                "name": "List Files",
                "category": "files",
                "description": "List a directory in the agent workspace",
                "tool_schema": {
                    "name": "list_files",
                    "description": "List the files and folders in a workspace directory (name, type, size) so you can see what exists before reading. Optional relative path; defaults to the workspace root.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative directory path (default: workspace root)."},
                        },
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "native", "pocketflow": "adapter",
                    "langroid": "adapter", "openai-agents-sdk": "adapter", "hermes": "adapter",
                    "agent-zero": "native", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.file_read",
            },
            {
                "id": "web_search",
                "name": "Web Search",
                "category": "search",
                "description": "Search the web via SearXNG or Perplexica",
                "tool_schema": {
                    "name": "web_search",
                    "description": "Search the web",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer", "default": 5},
                        },
                        "required": ["query"],
                    },
                },
                "frameworks": {
                    "smolagents": "native", "openclaw": "native", "pocketflow": "adapter",
                    "agent-zero": "native", "hermes": "adapter", "langroid": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "requires_services": ["searxng"],
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.web_search",
            },
            {
                "id": "code_exec",
                "name": "Code Execution",
                "category": "code",
                "description": "Execute Python code in a sandbox",
                "tool_schema": {
                    "name": "code_exec",
                    "description": "Execute Python code",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                        },
                        "required": ["code"],
                    },
                },
                "frameworks": {
                    "smolagents": "native", "openclaw": "adapter", "pocketflow": "adapter",
                    "agent-zero": "native", "hermes": "adapter", "langroid": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.code_exec",
            },
            {
                "id": "image_generation",
                "name": "Image Generation",
                "category": "media",
                "description": "Generate images via Stable Diffusion",
                "tool_schema": {
                    "name": "generate_image",
                    "description": "Generate an image from a text prompt",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "size": {"type": "string", "default": "512x512"},
                            "model": {"type": "string"},
                            "guidance_scale": {"type": "number", "default": 7.5},
                            "negative_prompt": {"type": "string", "default": ""},
                        },
                        "required": ["prompt"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.image_tool",
            },
            {
                "id": "list_image_models",
                "name": "List Image Models",
                "category": "media",
                "description": "Discover installed image-generation models",
                "tool_schema": {
                    "name": "list_image_models",
                    "description": "List installed image-generation models the agent can pick from",
                    "input_schema": {"type": "object", "properties": {}},
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.image_tool",
            },
            {
                "id": "http_request",
                "name": "HTTP Request",
                "category": "system",
                "description": "Make HTTP requests to external APIs",
                "tool_schema": {
                    "name": "http_request",
                    "description": "Make an HTTP request",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "method": {"type": "string", "default": "GET"},
                        },
                        "required": ["url"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "native",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.http_request",
            },
            {
                "id": "open_app",
                "name": "Open App",
                "category": "desktop",
                "description": "Open an app on the user's desktop so they can see it",
                "tool_schema": {
                    "name": "open_app",
                    "description": "Open (or focus) an app on the user's desktop (e.g. projects, images, chat).",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "app": {"type": "string", "description": "App id, e.g. 'projects', 'images', 'chat'."},
                            "props": {"type": "object", "description": "Optional deep-link props."},
                        },
                        "required": ["app"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.desktop_tools",
            },
            {
                "id": "read_layout",
                "name": "Read Desktop Layout",
                "category": "desktop",
                "description": "Read the user's screen size and open-window bounds (screen-aware control)",
                "tool_schema": {
                    "name": "read_layout",
                    "description": "Read the desktop layout: screen size + every open window's bounds and state.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.desktop_tools",
            },
            {
                "id": "arrange_windows",
                "name": "Arrange Windows",
                "category": "desktop",
                "description": "Arrange the user's desktop windows into a tidy layout",
                "tool_schema": {
                    "name": "arrange_windows",
                    "description": "Arrange open windows. Presets: tile-2, tile-3, center, cascade.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "preset": {
                                "type": "string",
                                "enum": ["tile-2", "tile-3", "center", "cascade"],
                                "description": "Layout preset.",
                            },
                        },
                        "required": ["preset"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.desktop_tools",
            },
            {
                "id": "create_project",
                "name": "Create Project",
                "category": "projects",
                "description": "Create a project for the user",
                "tool_schema": {
                    "name": "create_project",
                    "description": "Create a project and return its id.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Project title."},
                            "description": {"type": "string", "description": "Short summary."},
                        },
                        "required": ["name"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.project_tools",
            },
            {
                "id": "list_projects",
                "name": "List Projects",
                "category": "projects",
                "description": "List the user's projects so the agent can pick one",
                "tool_schema": {
                    "name": "list_projects",
                    "description": "List the user's projects (id, name, status) so you can pick the right project_id before adding tasks or images. Optional status filter: active (default), archived, all.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["active", "archived", "all"], "description": "Which projects to list (default active)."},
                        },
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.project_tools",
            },
            {
                "id": "list_tasks",
                "name": "List Tasks",
                "category": "projects",
                "description": "List a project's tasks so the agent can review progress or pick next",
                "tool_schema": {
                    "name": "list_tasks",
                    "description": "List a project's tasks (id, title, status) so you can review progress or pick the next one. Requires project_id (from list_projects/create_project); optional status filter (e.g. open, in_progress, done).",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string", "description": "Id from list_projects/create_project."},
                            "status": {"type": "string", "description": "Optional status filter (e.g. open, in_progress, done)."},
                        },
                        "required": ["project_id"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.project_tools",
            },
            {
                "id": "add_task",
                "name": "Add Task",
                "category": "projects",
                "description": "Add a task to a project's board",
                "tool_schema": {
                    "name": "add_task",
                    "description": "Add a to-do task to a project board (appears live).",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string", "description": "Id from create_project."},
                            "title": {"type": "string", "description": "Task title."},
                        },
                        "required": ["project_id", "title"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.project_tools",
            },
            {
                "id": "request_decision",
                "name": "Request Decision",
                "category": "agent",
                "description": "Ask the user a question via the Decisions inbox (human-in-the-loop)",
                "tool_schema": {
                    "name": "request_decision",
                    "description": "Ask the user a question (single_select/multi_select need options; approve_deny; free_text) that queues in their Decisions inbox until answered. Returns a decision_id; the answer arrives later.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "The question to ask."},
                            "type": {"type": "string", "enum": ["single_select", "multi_select", "approve_deny", "free_text"], "description": "Decision type."},
                            "options": {"type": "array", "items": {"type": "string"}, "description": "Choices (required for select types)."},
                            "context": {"type": "string", "description": "Optional background to help the user decide."},
                            "priority": {"type": "string", "enum": ["normal", "blocking"], "description": "'blocking' only if you cannot proceed without it."},
                            "from_agent": {"type": "string", "description": "Your agent name, shown as the asker."},
                        },
                        "required": ["question", "type"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.decision_tools",
            },
            {
                "id": "list_frameworks",
                "name": "List Agent Frameworks",
                "category": "agent",
                "description": "List the agent frameworks taOS can deploy, so the agent can offer one",
                "tool_schema": {
                    "name": "list_frameworks",
                    "description": "List the agent frameworks taOS can deploy (id, name, description, verification_status; beta = recommended). Use before offering to deploy/set up an agent so you name a real framework and prefer a beta one. Optional verified_only=true returns beta only.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "verified_only": {"type": "boolean", "description": "Only beta (recommended) frameworks. Default false."},
                        },
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.framework_tools",
            },
            {
                "id": "get_capabilities",
                "name": "Get Capabilities",
                "category": "agent",
                "description": "Report what this taOS install can do on its hardware/cluster (with unlock hints)",
                "tool_schema": {
                    "name": "get_capabilities",
                    "description": "Check what this taOS install can do on its current hardware + cluster (chat-small, chat-large, image-generation-gpu, embedding, tts, lora-training, ...) with availability + unlock hints. Use before promising a hardware-bound action. Optional available_only=true.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "available_only": {"type": "boolean", "description": "Only currently-available capabilities. Default false."},
                        },
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.capability_tools",
            },
            {
                "id": "list_store_apps",
                "name": "List Store Apps",
                "category": "agent",
                "description": "List installable Store apps/backends so the agent can offer to install one",
                "tool_schema": {
                    "name": "list_store_apps",
                    "description": "List installable things in the taOS Store (apps, models, services, backends, plugins) with whether each is installed, so you can offer to install what the user needs. Optional 'type' filter and 'query' search. Read-only; the user installs from the Store app.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "description": "Optional type filter (app, model, service, backend, plugin)."},
                            "query": {"type": "string", "description": "Optional search over name and description."},
                        },
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.store_tools",
            },
            {
                "id": "notify_user",
                "name": "Notify User",
                "category": "agent",
                "description": "Send the user a short notification in their bell (async heads-up)",
                "tool_schema": {
                    "name": "notify_user",
                    "description": "Send the user a brief notification that appears in their bell, for an async heads-up they should see even when not in your chat. For a question that needs an answer, use request_decision instead.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "The notification body (keep it short)."},
                            "title": {"type": "string", "description": "Optional short title; defaults to 'Agent update'."},
                            "level": {"type": "string", "enum": ["info", "success", "warning", "error"], "description": "Severity (default info)."},
                        },
                        "required": ["message"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.notify_tools",
            },
            {
                "id": "canvas_add_image",
                "name": "Add Image to Canvas",
                "category": "projects",
                "description": "Place a generated image on a project's canvas",
                "tool_schema": {
                    "name": "canvas_add_image",
                    "description": "Place a generated image on a project's ideas board.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string", "description": "Id from create_project."},
                            "image_ref": {"type": "string", "description": "The image_ref returned by generate_image."},
                            "alt": {"type": "string", "description": "Alt text."},
                        },
                        "required": ["project_id", "image_ref"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.project_tools",
            },
            {
                "id": "describe_image_capabilities",
                "name": "Describe Image Capabilities",
                "category": "media",
                "description": "See the cluster's image-generation tiers and tools (NPU/GPU/CPU)",
                "tool_schema": {
                    "name": "describe_image_capabilities",
                    "description": "List the hardware tiers (this host + cluster workers) and which image-generation tools/models each has loaded, so you can pick the best one before generate_image.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.cluster_tools",
            },
            {
                "id": "export_storybook",
                "name": "Export Storybook PDF",
                "category": "projects",
                "description": "Render a project's pages + illustrations into an illustrated storybook PDF",
                "tool_schema": {
                    "name": "export_storybook",
                    "description": "Assemble an illustrated children's-book PDF from a project's pages and the images you generated. Saved to the project's Files (exports/) and downloadable. Call this as the final step after generating the art.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string", "description": "Id from create_project."},
                            "title": {"type": "string", "description": "The book title (shown on the cover)."},
                            "pages": {
                                "type": "array",
                                "description": "Ordered pages. Each is {text, image_ref} where image_ref is a filename returned by generate_image.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string", "description": "The page's caption/story text."},
                                        "image_ref": {"type": "string", "description": "image_ref returned by generate_image for this page."},
                                    },
                                    "required": ["text"],
                                },
                            },
                            "cover_image_ref": {"type": "string", "description": "Optional cover image_ref; defaults to the first page's image."},
                            "author": {"type": "string", "description": "Optional author shown on the cover."},
                        },
                        "required": ["project_id", "title", "pages"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.project_tools",
            },
            {
                "id": "notes_list_shared_docs",
                "name": "List Shared Docs",
                "category": "notes",
                "description": "List the non-archived shared notes and lists the agent is a member of",
                "tool_schema": {
                    "name": "notes_list_shared_docs",
                    "description": "List the non-archived shared notes and lists this agent is a member of. Returns id, kind, title, and updated_at for each doc.",
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.notes_tools",
            },
            {
                "id": "notes_add_entry",
                "name": "Add Notes Entry",
                "category": "notes",
                "description": "Append a new entry to a shared note or list the agent is a member of",
                "tool_schema": {
                    "name": "notes_add_entry",
                    "description": "Append a new entry to a shared note or list this agent is a member of. Use notes_list_shared_docs first to get the doc_id.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "string", "description": "Id of the shared doc (from notes_list_shared_docs)."},
                            "text": {"type": "string", "description": "The entry text to append."},
                        },
                        "required": ["doc_id", "text"],
                    },
                },
                "frameworks": {
                    "smolagents": "adapter", "openclaw": "adapter", "pocketflow": "adapter",
                    "langroid": "adapter", "hermes": "adapter", "agent-zero": "adapter",
                    "openai-agents-sdk": "adapter", "generic": "adapter",
                },
                "install_method": "builtin",
                "install_target": "tinyagentos.tools.notes_tools",
            },

        ]

        for skill in defaults:
            await self._db.execute(
                """INSERT OR IGNORE INTO skills (id, name, category, description, tool_schema, frameworks,
                   requires_services, install_method, install_target, installed, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    skill["id"], skill["name"], skill["category"], skill["description"],
                    json.dumps(skill["tool_schema"]), json.dumps(skill["frameworks"]),
                    json.dumps(skill.get("requires_services", [])),
                    skill["install_method"], skill["install_target"],
                    time.time(),
                ),
            )
            # INSERT OR IGNORE leaves an existing row untouched, so an install
            # seeded by an earlier release keeps its stale tool_schema (e.g. the
            # pre-image_ref canvas_add_image contract). Refresh the code-owned
            # fields for builtin skills so existing installs converge on the
            # current definition. Scoped to install_method='builtin' so a user's
            # installed/customised skills are never overwritten.
            await self._db.execute(
                """UPDATE skills
                   SET name = ?, category = ?, description = ?, tool_schema = ?,
                       frameworks = ?, requires_services = ?, install_target = ?
                   WHERE id = ? AND install_method = 'builtin'""",
                (
                    skill["name"], skill["category"], skill["description"],
                    json.dumps(skill["tool_schema"]), json.dumps(skill["frameworks"]),
                    json.dumps(skill.get("requires_services", [])),
                    skill["install_target"], skill["id"],
                ),
            )
        await self._db.commit()

    async def list_skills(self, category: str | None = None) -> list[dict]:
        assert self._db is not None
        if category:
            cursor = await self._db.execute("SELECT * FROM skills WHERE category = ? AND installed = 1", (category,))
        else:
            cursor = await self._db.execute("SELECT * FROM skills WHERE installed = 1")
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [self._row_to_skill(dict(zip(cols, r))) for r in rows]

    async def get_skill(self, skill_id: str) -> dict | None:
        assert self._db is not None
        cursor = await self._db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return self._row_to_skill(dict(zip(cols, row)))

    def _row_to_skill(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "description": row["description"],
            "version": row["version"],
            "tool_schema": json.loads(row["tool_schema"] or "{}"),
            "frameworks": json.loads(row["frameworks"] or "{}"),
            "requires_services": json.loads(row["requires_services"] or "[]"),
            "requires_hardware": json.loads(row["requires_hardware"] or "{}"),
            "install_method": row["install_method"],
            "install_target": row["install_target"],
            "installed": bool(row["installed"]),
        }

    async def assign_skill(self, agent_id: str, skill_id: str, config: dict | None = None) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO agent_skills (agent_id, skill_id, enabled, config) VALUES (?, ?, 1, ?)",
            (agent_id, skill_id, json.dumps(config or {})),
        )
        await self._db.commit()

    async def unassign_skill(self, agent_id: str, skill_id: str) -> None:
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM agent_skills WHERE agent_id = ? AND skill_id = ?",
            (agent_id, skill_id),
        )
        await self._db.commit()

    async def get_agent_skills(self, agent_id: str) -> list[dict]:
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT s.*, a.enabled AS a_enabled, a.config AS a_config
               FROM agent_skills a
               JOIN skills s ON s.id = a.skill_id
               WHERE a.agent_id = ? AND a.enabled = 1""",
            (agent_id,),
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        result = []
        for r in rows:
            row_dict = dict(zip(cols, r))
            skill = self._row_to_skill(row_dict)
            skill["agent_config"] = json.loads(row_dict["a_config"] or "{}")
            result.append(skill)
        return result

    def is_compatible(self, skill: dict, framework: str) -> str:
        """Returns 'native', 'adapter', or 'unsupported'."""
        frameworks = skill.get("frameworks", {})
        return frameworks.get(framework, "unsupported")

    async def get_compatible_skills(self, framework: str) -> list[dict]:
        """Get all skills compatible with a framework."""
        all_skills = await self.list_skills()
        return [s for s in all_skills if self.is_compatible(s, framework) != "unsupported"]
