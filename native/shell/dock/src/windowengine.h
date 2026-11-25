// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>
#include <cstdio>
#include <QVector>

#include "windowinfo.h"

// The seam between the dock and the window engine (taos-window). The contract:
//   list      every window, minimised ones included
//   close|minimise|restore|toggle-minimise|focus|full|maximise|bring-here|reset-size <con id>
//   fullscreen <con id> on|off     move-to-space <con id> <space>     new-space <con id>
//   spaces    the list of spaces (taos-window spaces --json)
//   subscribe a change notification
// A minimised window carries the sway mark taos_min_<conid>; the engine keeps NO minimised state of
// its own, so a dock (or an engine) that restarts rebuilds from the compositor.
//
// FALLBACK MARKER: SwayEngine (swayengine.cpp) is the dock's own minimal implementation of this
// interface. Replace it by an adapter over `taos_window_core` once the window engine lands; nothing
// else in the dock changes. CliEngine (cliengine.cpp) already drives the real `taos-window` tool.
class WindowEngine : public QObject {
    Q_OBJECT
public:
    using QObject::QObject;
    virtual QString name() const = 0;
    virtual void start() = 0;
    virtual void refresh() = 0;                   // asynchronous; emits windowsChanged when the answer arrives
    virtual QVector<WindowInfo> windows() const = 0;
    virtual void close(int id) = 0;
    virtual void minimise(int id) = 0;
    virtual void restore(int id) = 0;
    virtual void toggleMinimise(int id) = 0;
    virtual void focus(int id) = 0;               // restores a minimised window first
    virtual void full(int id) = 0;                // toggles full size (the key binding's verb)
    // The window controls of the menus. The engine owns every geometry rule; the dock never computes one. An engine
    // that cannot do them (the built-in fallback) says so and does nothing.
    virtual void maximise(int) { unsupported("maximise"); }       // only enters full size
    virtual void restoreSize(int) { unsupported("restore"); }     // leaves full screen, then full size, back to the geometry before
    virtual void fullscreen(int, bool) { unsupported("fullscreen"); }   // sway's own fullscreen, on or off
    virtual void bringHere(int) { unsupported("bring-here"); }
    virtual void resetSize(int) { unsupported("reset-size"); }
    virtual void moveToSpace(int, const QString &) { unsupported("move-to-space"); }
    virtual void newSpace(int) { unsupported("new-space"); }
    virtual QVector<SpaceInfo> spaces() const { return {}; }
    virtual void refreshSpaces() {}               // asynchronous; emits spacesChanged when the answer arrives
    // The workspace that has focus (empty when unknown). Read from the compositor, never remembered.
    virtual QString focusedWorkspace() const { return {}; }
protected:
    void unsupported(const char *verb) const { fprintf(stderr, "taos-dock: the %s window engine cannot %s; install taos-window\n", qPrintable(name()), verb); }
public:
signals:
    void spacesChanged();
    void windowsChanged();
    void focusedWorkspaceChanged();
};
