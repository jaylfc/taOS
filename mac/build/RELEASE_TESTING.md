# taOS Mac App — Release Testing Checklist

Run on a fresh macOS 26 install (or a wiped data dir) before tagging a release.

## 1. Fresh install

- [ ] Open `taOS-X.Y.Z.dmg` → drag to Applications.
- [ ] Right-click `/Applications/taOS.app` → Open → confirm "open" in Gatekeeper dialog.
- [ ] Status-bar `taOS` icon appears.
- [ ] First-run wizard opens in **phone-shape mode**.
- [ ] Setup completes; Dock icon disappears after closing the window.

## 2. Container creation

- [ ] Create a project, add an agent.
- [ ] Open Terminal: `container ls` shows the new container.
- [ ] Send chat → response received.
- [ ] Trace store entry shows `host.containers.internal:4000` reachable.

## 3. Window-mode toggle

- [ ] `Cmd+Shift+M` switches phone ↔ fullscreen.
- [ ] In fullscreen, `Cmd+Q` is intercepted (no quit).
- [ ] In fullscreen, `Ctrl+→` moves to the next Space.
- [ ] In fullscreen, three-finger swipe moves Spaces.

## 4. Quit + relaunch

- [ ] Close window with the red traffic light.
- [ ] Dock icon hides; status-bar icon stays.
- [ ] `~/Library/Logs/taOS/server.log` keeps writing.
- [ ] "Quit taOS" from status bar → graceful shutdown logged → process gone.
- [ ] Relaunch → restores last window mode + last project.

## 5. Update path

- [ ] Stage a fake v0.1.1 DMG.
- [ ] Serve a local appcast: `cd staging && python -m http.server 8000`.
- [ ] In test build, set `SUFeedURL` to `http://127.0.0.1:8000/appcast.xml`.
- [ ] Sparkle prompts → EdDSA verifies → relaunches at v0.1.1.
- [ ] Re-run with a tampered DMG → install refused; `~/Library/Logs/taOS/sparkle.log` has a verification-failed entry; app stays on v0.1.0.

## 6. Failure isolation

- [ ] Kill FastAPI from Activity Monitor → status-bar icon goes yellow → "Restart Server" works.
- [ ] Stop the container daemon → existing containers listed but new creates fail with the documented note → restart daemon → recovers.
- [ ] Disconnect network → Sparkle silently skips next scheduled check.

## 7. Uninstall

- [ ] Trash `/Applications/taOS.app`.
- [ ] `~/Library/Application Support/taOS/` untouched.
- [ ] Reinstall the same DMG → no setup wizard, picks up where it left off.
- [ ] `brew uninstall --cask --zap taos` (when Cask exists) wipes the four `~/Library/...` dirs.

## Sign-off

Tester: ______________  Date: ______________
Version: ______________  All boxes checked: yes / no

Failures: file as GitHub issues with `mac-release` label and block release until resolved.
