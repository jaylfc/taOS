// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QMap>
#include <QString>

#include "windowmodel.h"

namespace TaosWin {

// Window style per workspace: "desktop" (windows float, the dock is shown) or "tiling" (windows tile, the dock
// hides). Persisted per person in ~/.config/taos/desktop.json:
//   {"default": "desktop", "workspaces": {"2": "tiling"}}
// A person with no file gets desktop everywhere.
struct DesktopModes {
    QString defaultMode = QStringLiteral("desktop");
    QMap<QString, QString> workspaces;
    QString modeFor(const QString &workspace) const { return workspaces.value(workspace, defaultMode); }
    bool operator==(const DesktopModes &o) const { return defaultMode == o.defaultMode && workspaces == o.workspaces; }
};

namespace Modes {
bool valid(const QString &m);                 // "desktop" or "tiling"
QString path();                               // $TAOS_DESKTOP_CONFIG, else $XDG_CONFIG_HOME/taos/desktop.json, else ~/.config/taos/desktop.json
// Missing or unreadable file: defaults (false only when the file exists but is not a JSON object).
bool read(const QString &path, DesktopModes *out, QString *error = nullptr);
bool write(const QString &path, const DesktopModes &m, QString *error = nullptr);   // atomic
}  // namespace Modes

// Commands for switching one workspace's windows (minimised ones are left alone):
//  desktop: every tiled window becomes floating and keeps its on-screen geometry;
//  tiling: every floating window is tiled; "full size" marks are cleared.
QStringList modeSwitchCommands(const Tree &t, const QString &workspace, const QString &mode);
// The rectangle each window of `workspace` should end up with when switching to desktop (tiled ones only).
QMap<int, QRect> modeSwitchTargets(const Tree &t, const QString &workspace);
// A window that just appeared on a desktop-mode workspace: float it at a sensible size, centred, cascaded.
// Empty when nothing is to be done (already floating, minimised, fullscreen, or the workspace is tiling).
// The rectangle newWindowCommands aims for (invalid: nothing to do).
QRect newWindowGeometry(const Tree &t, const Window &w, const DesktopModes &modes);
// What each app's window looked like when it was last closed, per output, so the next one opens there again:
//   {"HDMI-A-1": {"my.taos.studio": {"x": 100, "y": 80, "w": 1200, "h": 800}}}
// in ~/.config/taos/window-geometry.json ($TAOS_WINDOW_GEOMETRY overrides). A remembered rectangle is only used while it
// still fits the usable area of the output (and is within 90 % of it).
struct PlacementMemory {
    QMap<QString, QMap<QString, QRect>> byOutput;
    static QString path();
    bool read(const QString &file = path(), QString *error = nullptr);
    bool write(const QString &file = path(), QString *error = nullptr) const;   // atomic
    QRect recall(const QString &output, const QString &appId) const { return byOutput.value(output).value(appId); }
    // Remembers a closed window's rectangle; false (nothing stored) for windows that are not worth remembering.
    bool remember(const Window &w);
};
// The same placement for a window that already floats, at the size its application asked for (`preferred`, kept
// unless it does not fit the free area). Invalid `preferred`: the default 60 % x 70 %.
//
// Rules, for every new floating window (or a tiled one that is being floated): never more than 90 % of the usable area
// (bar and dock excluded) in either direction; always fully inside it; the app's remembered rectangle for this output when
// it still fits; else centred, cascaded 32 px from the last window of the same app; a dialog (a smaller window of an app
// that already has one open on this workspace) is centred on that window.
QRect placeNewWindow(const Tree &t, const Window &w, const DesktopModes &modes, const QSize &preferred,
                     const PlacementMemory *memory = nullptr);
QStringList newWindowCommands(const Tree &t, const Window &w, const DesktopModes &modes);

}  // namespace TaosWin
