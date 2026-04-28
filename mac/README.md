# taOS Mac App build

Build pipeline for the macOS `.app` bundle (Apple Silicon, macOS 26+).

See `docs/superpowers/specs/2026-04-28-taos-mac-app-c1-design.md` for the full design.

## Quick start

    ./mac/build/build.sh --version 0.1.0 --output dist/

Produces `dist/taOS.app`, `dist/taOS-0.1.0.dmg`, and `dist/taOS-0.1.0.dmg.sig`.

## Layout

- `launcher/` — Swift Package for the native launcher
- `build/` — shell pipeline (orchestrator + step scripts)
- `appcast/` — Sparkle appcast.xml + EdDSA public key
