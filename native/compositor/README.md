# taOS desktop compositor (swayfx on wlroots 0.20)

The taOS session compositor is swayfx 0.6.1 (sway 1.12 with blur, shadows and
rounded corners) built against wlroots 0.20.2. It is carried as a private tree
under `/opt/taos/compositor` instead of using Debian's sway, because Debian 13
ships wlroots 0.18 and libraries too old for wlroots 0.20. Per-window capture
(`ext-foreign-toplevel-image-capture-source`, used for window thumbnails) and
`ext-workspace-v1` need wlroots 0.20, and the sandbox identity fields
(`sandbox_engine`, `sandbox_app_id`, `sandbox_instance_id` in `get_tree`) that
the app-group design relies on are part of sway 1.12's IPC. Nothing here
touches Debian's `/usr/bin/sway` or any system library.

## Versions (all sha256-pinned in `build.sh`)

| component | version | sha256 |
|---|---|---|
| wayland | 1.24.0 | 82892487a01ad67b334eca83b54317a7c86a03a89cfadacfef5211f11a5d0536 |
| wayland-protocols | 1.50 | 4460c20cfcf68b994a5ee206ed15fc19ad312f16c96075186e83b0e3f4aa48a5 |
| libdrm | 2.4.131 | 45ba9983b51c896406a3d654de81d313b953b76e6391e2797073d543c5f617d5 |
| pixman | 0.46.4 | d09c44ebc3bd5bee7021c79f922fe8fb2fb57f7320f55e97ff9914d2346a591c |
| wlroots | 0.20.2 | 80c567d2ed4efb2cfa6b077f22c7a710d9c78ba4e185a75e8694001114af0733 |
| scenefx | 0.5 | 0fa8ecca0e310f813efd052624c5ed7d9153d6a0fdead5cc957d34c07f9a86c6 |
| swayfx | 0.6.1 | 7afe1e29a3508d3ab724d5ef029df48fcbf770c4af48c294e3051571aa835dcb |

Carried because Debian's are too old for wlroots 0.20.2: wayland (needs
>= 1.24), libdrm (>= 2.4.129), wayland-protocols (>= 1.47), pixman (>= 0.46).
Everything else (libinput, xkbcommon, libseat, EGL, cairo, pango, ...) comes
from Debian. The staged tree is about 13 MB.

swayfx 0.6.1 has no `animations { }` block. Its only animation setting is
`animation_duration_ms <0-5000>`; the blur options (`blur enable`,
`blur_radius`, `blur_passes`, `shadows`, `corner_radius`, `layer_effects`) are
supported.

## Build

On a Debian 13 amd64 host with the apt build dependencies (`apt build-dep
sway`, plus meson, cmake, curl). No root is needed.

    PREFIX=/opt/taos/compositor nice -n 10 \
      native/compositor/build.sh <staging-dir> <work-dir> -j8

Files are installed with `DESTDIR=<staging-dir>`, so the result is
`<staging-dir>/opt/taos/compositor`. The script downloads each tarball,
verifies its sha256, builds in dependency order, links every binary and
library with RPATH `PREFIX/lib`, and installs `sway`, `swaymsg`, `swaynag` and
`swaybar` into `PREFIX/bin`. It stops on any failure and ends by checking that
no carried library (wayland, drm, pixman, wlroots, scenefx) resolves outside
PREFIX; it exits non-zero otherwise. The ldd check points the loader at the
staged copy for that check only, since PREFIX does not exist yet.

## Carried patch

Licence: swayfx and sway are MIT (Copyright 2016-2017 Drew DeVault and contributors), wlroots and scenefx
are MIT. The patch below modifies swayfx and is therefore distributed under swayfx's MIT licence, not
the AGPL that covers the rest of this tree. See `../THIRD-PARTY.md`.

`patches/swayfx-0.6.1-minimize-registration.patch` (applied by `build.sh`, sha256-pinned there) fixes a crash in
swayfx 0.6.1: with `scratchpad_minimize`, the foreign-toplevel minimise listener was registered only if the option
was on when a window mapped, but removed whenever it was on when the window was destroyed, so a reload that turned
the option on while windows were mapped crashed the compositor (`wl_list_remove` in `handle_foreign_destroy`) when
they closed, and turning it off left a dangling listener. The patch registers the listeners always, makes the handlers
(`handle_foreign_minimize` in `sway/tree/view.c`, `handle_request_minimize` in `sway/desktop/xdg_shell.c`) check the
option when the request arrives, and guards the removal with `.notify`. With the patched build the option is safe
under any reload. `sway/46-taos-window-minimize.conf` turns the option on; install it only after the patched
compositor is running. Needs the `patch` tool at build time.

## Install

As root, on the target machine:

    cp -a <staging-dir>/opt/taos/compositor /opt/taos/

The binaries find their libraries through RPATH, so no `LD_LIBRARY_PATH` is
needed.

## Roll back

The session uses `/opt/taos/compositor/bin/sway` when it exists and falls back
to `/usr/bin/sway` when it is absent. To roll back, remove or rename
`/opt/taos/compositor` and start a new session.

## Update

1. Change the version, URL and sha256 line in `COMPONENTS` in `build.sh` (and
   the table above). Check the dependency floors: a new wlroots may need newer
   wayland, libdrm, wayland-protocols or pixman, and swayfx must match the
   scenefx and wlroots series it names.
2. Build to a fresh staging dir and check that the ldd step passes.
3. Verify headless with the staged binary (`WLR_BACKENDS=headless
   WLR_LIBINPUT_NO_DEVICES=1`, a private `XDG_RUNTIME_DIR`, and
   `LD_LIBRARY_PATH=<staging-dir>/opt/taos/compositor/lib` for this check
   only): `swaymsg -t get_version`, `wayland-info` listing
   `ext_workspace_manager_v1` and the foreign toplevel image capture source,
   `grim -T <foreign_toplevel_identifier>`, and `sway --validate` on the
   session config.
4. Install as above, keeping the previous tree as `/opt/taos/compositor.old`
   until the new session is confirmed good.
