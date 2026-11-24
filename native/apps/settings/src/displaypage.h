// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>
#include <memory>

#include "displaycontroller.h"
#include "outputwatcher.h"

namespace TaosSettings {

// The display engine (DisplayController, the very code taos-display and the bar's Display menu run) as a page
// model: it keeps the output list current (OutputWatcher), says whether there is a compositor to talk to,
// and runs the "Keep this setting?" trial through the controller. Nothing is stored here.
class DisplayPage : public QObject {
    Q_OBJECT
    Q_PROPERTY(QObject *controller READ controllerObject CONSTANT)
    Q_PROPERTY(QString state READ state NOTIFY stateChanged)       // "loading" | "ready" | "empty" | "error"
    Q_PROPERTY(QString message READ message NOTIFY stateChanged)
public:
    // `sway` is borrowed when given (tests); else the real swaymsg is used. `watch`: follow monitor events.
    DisplayPage(SwayRunner *sway, const DisplayConfigFile &cfg, const QString &drmRoot, std::function<qint64()> nowMs,
                bool watch, QObject *parent = nullptr);
    DisplayController *controller() { return &m_ctl; }
    QObject *controllerObject() { return &m_ctl; }
    QString state() const { return m_state; }
    QString message() const { return m_message; }
    Q_INVOKABLE void reload();
signals:
    void stateChanged();
private:
    void update(bool ok);
    SwayRunner *m_sway;
    DisplayController m_ctl;
    std::unique_ptr<OutputWatcher> m_watcher;
    QString m_state = QStringLiteral("loading"), m_message;
};

} // namespace TaosSettings
