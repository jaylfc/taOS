#pragma once
#include <QByteArray>
#include <QRect>
#include <QString>
#include <QVector>

// One toplevel window as sway describes it (a leaf container of get_tree).
struct WinInfo {
    int conId = 0;
    QString appId;            // Wayland app_id, or the X11 class for XWayland windows
    QString title;
    QString workspace;        // workspace name
    int workspaceNum = -1;    // sway's number for the workspace, -1 when it has none
    QString output;
    bool focused = false;
    bool urgent = false;
    bool floating = false;
    bool minimised = false;   // parked by taos-window (sway scratchpad); `workspace` is where it will come back
    QRect rect;               // the container rect in layout coordinates
    QString identifier;       // foreign_toplevel_identifier (ext-foreign-toplevel-list)
    int pid = 0;
    bool operator==(const WinInfo &o) const;
};

struct WsInfo {
    int id = 0;
    QString name;
    int num = -1;
    QString output;
    bool focused = false;     // the focused workspace of the seat
    bool visible = false;     // the current workspace of its output
    bool urgent = false;
    QRect rect;
};

struct OutInfo {
    QString name;
    QRect rect;
    QString currentWorkspace;
    bool focused = false;
};

struct Snapshot {
    QVector<WinInfo> windows;     // in tree order
    QVector<WsInfo> workspaces;   // in tree order (outputs, then workspaces)
    QVector<OutInfo> outputs;
    int focusedConId = 0;         // 0 when no window has focus
    QString focusedWorkspace;
    QString focusedOutput;
    // Windows in the order sway itself would cycle them: the focused window first,
    // then the rest by each workspace's own focus stack. Seeds the MRU list.
    QVector<int> seedOrder;
};

namespace SwayTree {
// Parses the payload of `get_tree`. Returns false (and fills error) on malformed JSON.
bool parse(const QByteArray &json, Snapshot *out, QString *error = nullptr);
// Parses the node of a window event ("container"), for patching a known window.
bool parseWindowNode(const QByteArray &json, WinInfo *out);
}
