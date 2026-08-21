# Agent Manual Source Index

<!-- Lists source files in compile order. Edit these files, not docs/taos-agent-manual.md. -->

## Compile order

Run `python3 scripts/build-agent-manual.py` to compile these into `docs/taos-agent-manual.md`.

| File | Contents |
|---|---|
| `00-identity.md` | Who the taOS agent is, persona, the "speak as taOS" voice |
| `01-rules.md` | Absolute rules, the do-not-know fallback line, hard things never to do, and the mechanical-simple-auditable design law |
| `02-what-is-taos.md` | One-paragraph product description |
| `03-facts.md` | Ports, frameworks, URLs, and install command facts table |
| `04-apps.md` | One-line descriptions of every taOS app |
| `05-chat.md` | Mentions, quiet/lively mode, task verbs, slash commands |
| `06-updates-privacy.md` | Update flow, anonymous install ping, privacy answers |
| `07-after-update.md` | Breakage-log-first troubleshooting for post-update reports |
| `08-answer-templates.md` | Canned answer shapes for common questions |
| `09-os-control.md` | Driving the desktop: open_app / arrange_windows tools |
| `10-image-prompting.md` | Writing good prompts for the generate_image tool |
| `11-files-api.md` | Project Files REST API for agents: upload (multipart), list, fetch, and the one-write principle |
| `12-memory-mode-both.md` | Memory mode `both`: framework memory for live state, taOSmd for durable facts, turn-boundary rules |
| `13-memory-mode-framework.md` | Memory mode `framework`: native memory only, no taOSmd calls, redeploy clears all |
| `14-memory-mode-taosmd.md` | Memory mode `taosmd`: taOSmd only, for frameworks with no native memory, single source of truth |

## Companion skills

- **taos-agent** (`../../.claude/skills/taos-agent/SKILL.md`): the actionable skill for the
  OS-native taOS agent that operates the host desktop on the user's behalf. Consolidates the
  OS-operation content from this manual (desktop and window control, apps, projects, files, memory,
  notes, chat conventions, image generation, and answering). Features the hard rule that all
  desktop driving goes only through `POST /api/desktop/command` + `POST /api/desktop/screenshot`.
  Load this when you are the built-in taOS agent acting on the host desktop.
- **taos-development-skill** (`../../.claude/skills/taos-development-skill/SKILL.md`): the skill
  for contributors developing on the taOS codebase (Git workflow, testing, architecture, PR flow).
  This is codebase work, not OS operation.
