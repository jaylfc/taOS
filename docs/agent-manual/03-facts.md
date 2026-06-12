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
| LiteLLM (model routing) | port 7834 on new installs; 4000 on installs from before June 2026 |
| Agent frameworks | OpenClaw (default), Hermes, SmolAgents, Langroid, PocketFlow, OpenAI Agents SDK |
| Memory system | taOSmd, long-term memory shared by all agents |
| Install command | `curl -fsSL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-server.sh \| sudo bash` |
| Community | github.com/jaylfc/tinyagentos/discussions |
| Bug reports | github.com/jaylfc/tinyagentos/issues |

Old installs keep their old ports automatically. Users never need to change ports by hand.
