#pragma once
// Needs Qt Qml (TAOSQT_WITH_QML).
#include <QObject>
#include <QPointer>
#include <QWindow>

#include "taosqt/global.h"

namespace Taos {

/// The buttons of the taOS window title bar act through this. Under sway it runs the same engine as the
/// `taos-window` command (native/shell/window) on this application's own window, found by process id and app id
/// in sway's tree; anywhere else (another compositor, offscreen, tests) it falls back to Qt's own window calls.
/// Minimising, restoring and full size therefore leave their state in the compositor, not in the app.
class TAOSQT_EXPORT WindowControl : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QWindow *window READ window WRITE setWindow NOTIFY windowChanged)
    /// True when a sway socket is available (the engine is used).
    Q_PROPERTY(bool underSway READ underSway CONSTANT)
public:
    explicit WindowControl(QObject *parent = nullptr);
    QWindow *window() const { return m_window; }
    void setWindow(QWindow *w);
    bool underSway() const;

    Q_INVOKABLE void close();
    Q_INVOKABLE void minimise();
    /// Toggle full size: the window fills the usable area of its workspace (the bar and the dock stay) and a
    /// second call puts it back. Plain Qt fallback: maximised / normal.
    Q_INVOKABLE void toggleFull();

signals:
    void windowChanged();

private:
    
    QPointer<QWindow> m_window;
};

} // namespace Taos
