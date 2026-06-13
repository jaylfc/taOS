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

**"How do I add another machine to the cluster?"** Open the Cluster app on your main taOS, then on the other machine run the worker script from the Cluster app's add-machine instructions. The new machine shows a six digit pairing code; approve it in the Cluster app and it joins the mesh.

**"What models can I run on my hardware?"** Open the Models app: the catalog marks what fits your detected hardware. Small boards run quantized 1 to 3 billion parameter models well; an 8GB board handles 7B quantized; GPUs and Apple Silicon open up larger models. Cloud models work on anything once you add a provider key.

**"How do I back up taOS?"** Your data lives in the data directory of the install (agents, chats, memory, settings). Settings has a backups section; copying the whole data directory while taOS is stopped is also a complete backup.

**"Where do I report a bug?"** github.com/jaylfc/tinyagentos/issues, with the error text and what hardware you are on. If something broke right after an update, mention that; there is a known-breakages log the developers check first.

**"Can taOS work fully offline?"** Yes. With local models installed (rkllama or Ollama backends), every part of taOS runs on your network with no internet. Internet is only needed to download models, install apps from the store, check for updates, and use cloud model providers.
