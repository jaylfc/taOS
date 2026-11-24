// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QByteArray>
#include <QHash>
#include <QPoint>
#include <QRect>
#include <QSet>
#include <QSize>
#include <QString>
#include <QStringList>
#include <QVector>

#include "frameconfig.h"

// One toplevel as the strip needs it, read from sway's get_tree (it needs more than taos-window's list: the title
// bar rectangle, visibility, the stacking order and the output).
struct FrameWindow {
    int id = 0;
    QString appId;
    QString title;
    int pid = 0;
    bool focused = false;
    bool floating = false;
    bool fullscreen = false;      // sway fullscreen (not "full size")
    bool inTabs = false;          // child of a tabbed or stacked container (unsupported: sway draws tab titles there)
    bool visible = false;         // sway: drawn on a visible workspace right now
    bool parked = false;          // in the scratchpad (minimised)
    bool minimisedMark = false;   // carries taos_min_<id> (informational: only being parked hides the strip)
    QString border;               // normal | pixel | none | csd
    QRect rect;                   // the content rectangle (below the title bar)
    QRect deco;                   // the title bar rectangle (absolute, derived from rect), empty when sway draws none
    QString output;               // output name; empty for the scratchpad
    QRect outputRect;
    QString workspace;
};

struct FrameTree {
    QVector<FrameWindow> windows;   // back to front: tiled windows, then floating ones in stacking order
    const FrameWindow *find(int id) const;
};
bool parseFrameTree(const QByteArray &json, FrameTree *out, QString *error = nullptr);

// The three round buttons, in the strip's own coordinates. One source of truth for the layout, the hit regions, the
// QML and the tests.
namespace StripLayout {
constexpr int kBarHeight = 32;    // the strip, as TaosTitleBar
constexpr int kHit = 24;          // one button's hit area (the disc is 14 px inside it)
constexpr int kGap = 4;
constexpr int kInset = 8;         // from the window edge
struct Button { QString name; QRect rect; };     // name: close | minimise | full
QSize surfaceSize();              // the three buttons together
QVector<Button> buttons(bool right);             // in drawing order, left to right
QString buttonAt(bool right, const QPoint &p);   // "" when between or outside
QString labelFor(const QString &button, const QString &windowTitle);   // "Close <title>"
}

struct Strip {
    int id = 0;
    QString title;
    QString appId;
    QString output;
    QPoint pos;                   // top left in layout coordinates
    QPoint margin;                // top left from the output's top left (the layer-shell margins)
    QSize size;
    bool right = false;           // buttons on the right
    bool focused = false;
    bool show = false;
    QString reason;               // why it is hidden: minimised fullscreen hidden no-titlebar moving occluded
    QRect surface() const { return QRect(pos, size); }
};

struct PlanInput {
    bool buttonsRight = false;
    qint64 nowMs = 0;
    int settleMs = 150;                       // a window that moved less than this ago hides its buttons
    QHash<int, qint64> changedAt;             // when each window's geometry last changed
};
// One strip per framed window, shown or not (with the reason).
QVector<Strip> planStrips(const FrameTree &tree, const FrameConfig &cfg, const PlanInput &in);

// sway commands that give framed windows a title bar to paint over (and take it from windows that no longer match).
// `framed` is the set this daemon framed before; the result updates it.
QStringList borderCommands(const FrameTree &tree, const FrameConfig &cfg, QSet<int> *framed);

// Keeps sway's own title bar exactly the strip height: given the height sway draws now with the font px size and
// vertical padding in force, what to set. Converges in two or three steps whatever the font.
struct TuneStep { int fontSize = 0; int vPad = 0; bool done = false; };
TuneStep tuneTitleBar(int observedHeight, int fontSize, int vPad, int target = StripLayout::kBarHeight);
