# Updates and Privacy

<!-- Update flow, anonymous install ping, and how to answer privacy questions. -->

## Updates (and the privacy question)

- taOS checks for updates about once an hour and shows a notification when one is ready. Install it via Settings then Updates then Install Update.
- The update check also reports an anonymous install count to taos.my: a random ID, the version, and the platform. No names, no emails, no IP addresses are stored. Turn it off in Settings or with `TAOS_NO_UPDATE_PING=1`. Updates keep working either way.
- If a user asks "is taOS phoning home": answer yes, exactly one anonymous update-and-count ping, here is how to turn it off, and updates do not depend on it.
