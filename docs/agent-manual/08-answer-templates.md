# Answer Templates

<!-- Canned answer shapes for the most common questions. -->

## Answer templates (use these shapes)

**"How do I add an agent?"** — Open the Agents app, press the + button, pick a name, framework, and model. taOS builds the container and starts it.

**"How do I add an API key?"** — Open the Providers app, press Add Provider, choose the type, paste the key, save. New models appear in the Models app.

**"Agent can't reach its model / chat gives no answer."** — First: open Activity and look for red errors. If taOS restarted in the last few minutes, the model router may still be warming up; wait a minute and try again. If it persists, restart the agent from the Agents app. Still stuck: community page.

**"How do I get a shell in an agent container?"** — Use the shell shortcut in the Agents app. Host-side fallback: `incus exec taos-agent-<name> -- bash` (LXC) or `docker exec -it taos-agent-<name> bash` (Docker). Never `incus console`.

**"Can you build me an app/widget?"** — Not yet from me. A safe area for user-made apps, a My Apps manager, and agent-built apps are being built right now (the App Runtime work). Today: apps come from the Store, and feature requests are very welcome on the community page.

**"Is my data private?"** — Yes. Everything runs on your hardware. Agents, chats, files, and memory stay local. Only two things ever leave: cloud model calls IF you added a cloud provider, and one anonymous update ping you can turn off.

**"Something failed to install."** — taOS is in beta and some app and model manifests have not been tried on every hardware combination. Open an issue with the name of the thing and the error text; manifest fixes usually ship the same day.
