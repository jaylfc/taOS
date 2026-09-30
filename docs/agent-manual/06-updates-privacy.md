# Updates and Privacy

<!-- Update flow, anonymous install ping, and how to answer privacy questions. -->

## Updates (and the privacy question)

- taOS checks for updates hourly and shows a notification when one is ready. Install via Settings then Updates then Install Update.
- The update check reports an anonymous install count (random ID, version, platform). No names, emails, or IPs are stored. Turn it off with `TAOS_NO_UPDATE_PING=1`. Updates work either way.
- If a user asks "is taOS phoning home": answer yes, exactly one anonymous update-and-count ping, here is how to turn it off, and updates do not depend on it.
