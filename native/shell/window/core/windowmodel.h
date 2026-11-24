// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QJsonObject>
#include <QList>
#include <QRect>
#include <QString>
#include <QStringList>
#include <QVector>

namespace TaosWin {

// Everything that makes a window "minimised" or "full size" is kept in the compositor, as sway marks on the
// window itself, so it survives the death of any taos process:
//   taos_min_<conid>        the window is minimised (it is parked in sway's scratchpad)
//   taos_at_<ms>            when (ms since epoch): orders "restore the last minimised window"
//   taos_ws_<name>          the workspace it came from (name percent-encoded)
//   taos_mfl                it was floating when minimised (absent: it was tiled)
//   taos_mfs                it was fullscreen when minimised
//   taos_geo_<x>_<y>_<w>_<h>  its floating geometry when minimised
//   taos_full               "full size" is on (see ops)
//   taos_ftiled             ... and it was tiled before, so reverting makes it tiled again
//   taos_fgeo_<x>_<y>_<w>_<h> ... its geometry before
namespace Marks {
QString encodeName(const QString &name);    // keeps [A-Za-z0-9._-], everything else %XX
QString decodeName(const QString &enc);
QString geometry(const QString &prefix, const QRect &r);   // "taos_geo_10_20_300_200"
bool parseGeometry(const QString &mark, const QString &prefix, QRect *out);
}  // namespace Marks

struct Window {
    int id = 0;                 // sway container id
    QString appId;              // Wayland app_id, or the X11 class
    QString title;
    int pid = 0;
    QString workspace;          // minimised: the workspace it will come back to
    QString output;             // minimised: that workspace's output when it still exists, else empty
    bool focused = false;
    bool minimised = false;
    bool floating = false;      // minimised: whether it was floating when minimised
    bool fullscreen = false;    // sway's own fullscreen (not "full size")
    // Not part of the JSON contract:
    bool inScratchpad = false;  // parked in sway's scratchpad right now
    bool taosMinimised = false; // ... by us (carries taos_min_<id>)
    bool fullSize = false;      // taos_full
    bool fullWasTiled = false;
    bool minWasFullscreen = false;
    bool minWasFloating = false;
    qint64 minimisedAt = 0;
    QRect rect;                 // the container rect
    QRect savedGeometry;        // from taos_geo_ (minimised) or empty
    QRect fullSavedGeometry;    // from taos_fgeo_
    QStringList marks;          // all marks, ours and others
    QJsonObject toJson() const; // the contract: id app_id title pid workspace focused minimised floating fullscreen output
    bool operator==(const Window &o) const;
};

struct Workspace {
    QString name;
    QString output;
    QRect rect;                 // the usable area: output minus layer-shell reserved space (bar, dock)
    bool focused = false;
    bool visible = false;
};

struct Tree {
    QVector<Window> windows;        // tree order, scratchpad windows last
    QVector<Workspace> workspaces;  // real workspaces only
    QString focusedWorkspace;
    int focusedId = 0;
    const Window *find(int id) const;
    const Workspace *workspace(const QString &name) const;
    // The most recently minimised window carrying taos_min, or null.
    const Window *lastMinimised() const;
};

bool parseTree(const QByteArray &json, Tree *out, QString *error = nullptr);
bool isOurMark(const QString &mark);   // starts with "taos_"

struct WindowEvent {
    QString kind;      // new close focus title minimise restore mode
    Window window;
    Window before;     // minimise, restore: the window as it was in the previous snapshot (not in the JSON)
    QString workspace; // mode events: which workspace
    QString mode;      //   and its new style: desktop or tiling
    QJsonObject toJson() const;
};
// What changed between two snapshots, in a stable order. Windows are matched by id.
QList<WindowEvent> diffWindows(const Tree &before, const Tree &after);

// The window of this process: by pid, else by app_id (the focused one first). Null when none.
const Window *findOwn(const Tree &t, int pid, const QString &appId);

}  // namespace TaosWin
