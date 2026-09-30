# Facts

<!-- Ports, frameworks, URLs, and install command. Quote these exactly in answers. -->

## Facts table (quote these exactly)

| Thing | Fact |
|---|---|
| Desktop URL | `http://<host>:6969` (or `http://taos.local:6969` with mDNS) |
| Controller port | 6969 |
| Browser proxy port | 6970 |
| qmd model service | port 7832 |
| rkllama (NPU models) | port 7833 on new installs; 8080 on installs from before June 2026 |
| Model routing | LLM gateway, default (you: `127.0.0.1:4000/v1`); LiteLLM 7834 (4000 pre-June 2026) |
| Agent frameworks | OpenClaw (default), Hermes, SmolAgents, Langroid, PocketFlow, OpenAI Agents SDK |
| Memory system | taOSmd, long-term memory shared by all agents |
| Install command | `curl -fsSL https://raw.githubusercontent.com/jaylfc/taOS/master/scripts/install-server.sh \| sudo bash` |
| Community | github.com/jaylfc/taOS/discussions |
| Bug reports | github.com/jaylfc/taOS/issues |

Old installs keep their old ports automatically. Users never need to change ports by hand.
