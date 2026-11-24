### Fixed

- **taOS agent skill names the Routines app correctly (#2485 item 9)** — the skill's bundled-app list still called the app "Tasks" after its rename; the app registry (`desktop/src/registry/app-registry.ts`) has `id: "tasks"` displayed as "Routines", so an agent following the skill would look for an app that does not exist under that name.
