// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QByteArray>
#include <QString>
#include <QVector>

// One toplevel window as the window engine reports it (taos-window list --json).
struct WindowInfo {
    int id = 0;               // sway con id
    QString appId;            // Wayland app_id, or the X11 class
    QString title;
    int pid = 0;
    QString workspace;
    bool focused = false;
    bool minimised = false;   // lives in the compositor: the mark taos_min_<id>
    bool floating = false;
    bool fullscreen = false;
    bool fullSize = false;    // maximised: the window fills the usable area (`full_size` in the engine's list)
    QString output;
    bool operator==(const WindowInfo &o) const;
    bool operator!=(const WindowInfo &o) const { return !(*this == o); }
};

// One space (sway workspace), as `taos-window spaces --json` lists it.
struct SpaceInfo {
    QString name;
    bool focused = false;
    bool visible = false;
    int windows = 0;
    bool operator==(const SpaceInfo &o) const { return name == o.name && focused == o.focused && visible == o.visible && windows == o.windows; }
};

namespace WindowJson {
// Parses `taos-window spaces --json`.
bool parseSpaces(const QByteArray &json, QVector<SpaceInfo> *out, QString *error = nullptr);
// Parses the contract's `taos-window list --json`: an array of objects with
// id, app_id, title, pid, workspace, focused, minimised, floating, fullscreen, output.
bool parseList(const QByteArray &json, QVector<WindowInfo> *out, QString *error = nullptr);
QByteArray toJson(const QVector<WindowInfo> &w);
// Parses sway's get_tree payload into the same list. A window is minimised when it carries
// the mark taos_min_<its con id>. This is how the dock rebuilds everything after a restart.
bool parseSwayTree(const QByteArray &json, QVector<WindowInfo> *out, QString *error = nullptr);
QString minMark(int conId);
// The focused workspace name from sway's get_workspaces payload.
bool focusedWorkspace(const QByteArray &json, QString *name);
}
