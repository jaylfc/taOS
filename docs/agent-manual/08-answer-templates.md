# Answer Templates

<!-- Canned answer shapes for the most common questions. -->

## Answer templates (use these shapes)

**"How do I add an agent?"** — Open the Agents app, press +, pick name, framework, model. taOS builds the container and starts it.

**"How do I add an API key?"** — Open Providers, Add Provider, choose type, paste key, save. New models appear in Models.

**"Agent can't reach its model."** — Check Activity for red errors. If taOS restarted recently, wait a minute for the model router to warm up. Restart the agent from Agents. Still stuck: community page.

**"How do I get a shell in a container?"** — Shell shortcut in Agents app. Host fallback: `incus exec taos-agent-<name> -- bash`. Never `incus console`.

**"Can you build me an app?"** — Not yet. Apps come from the Store today. Feature requests are welcome on the community page.

**"Is my data private?"** — Your chats, files, and memory stay on your hardware and are never uploaded. The only thing that sends your content out is a cloud model call, and only if you added a cloud provider. taOS still uses the internet for model downloads, app installs, and update checks, but those carry no personal data.

**"Something failed to install."** — taOS is in beta and some manifests have not been tried on every hardware combination. Open an issue with the name and error text.

**"How do I add another machine to the cluster?"** — Open Cluster on your main taOS, then on the other machine run the worker script from Cluster's add-machine instructions. Approve the pairing code in Cluster.

**"What models can I run?"** — Open Models: the catalog marks what fits your hardware. Small boards run 1-3B quantized well; 8GB handles 7B quantized; GPUs and Apple Silicon handle larger. Cloud models work on anything with a provider key.

**"How do I back up taOS?"** — Copy the whole data directory while taOS is stopped. Settings also has a backups section.

**"Where do I report a bug?"** — github.com/jaylfc/taOS/issues with error text and hardware. If it broke after an update, mention that.

**"Can taOS work fully offline?"** — Yes, with local models (rkllama or Ollama). Internet only needed to download models, install apps, check updates, and use cloud providers.
