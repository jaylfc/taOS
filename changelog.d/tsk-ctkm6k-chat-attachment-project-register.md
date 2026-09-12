### Added
- Chat attachments can auto-register into a project's Files when sent from a project-scoped chat, and a new `/api/chat/attachments/resolve` endpoint returns a tri-state `in_files_state` (`registered`, `not-registered`, `unknown`) so the caller can distinguish "in Files" from "nobody asked".
