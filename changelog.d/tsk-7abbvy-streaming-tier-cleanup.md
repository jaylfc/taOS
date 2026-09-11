### Fixed

- Removed the unused streaming tier from the app catalog. The registry only loads apps from the `agents`, `models`, `services`, and `plugins` directories, making the `streaming` tier dead content that was never advertised or installable. This eliminates the issue where 12 of 13 Dockerfiles were stubs and the registry never reported any streaming-app entries.