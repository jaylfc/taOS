#pragma once
#include <QObject>
#include <QVector>

#include "windowinfo.h"

// The seam between the dock and the window engine (taos-window). The contract:
//   list      every window, minimised ones included
//   close|minimise|restore|toggle-minimise|focus|full <con id>
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
    virtual void full(int id) = 0;
    // The workspace that has focus (empty when unknown). Read from the compositor, never remembered.
    virtual QString focusedWorkspace() const { return {}; }
signals:
    void windowsChanged();
    void focusedWorkspaceChanged();
};
