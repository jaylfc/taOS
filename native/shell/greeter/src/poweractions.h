// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>
#include <QTimer>

// PowerActions: Sleep, Restart and Shut Down with a one click confirmation.
// activate(action) first arms the action for 4 seconds (the button reads
// "Restart?"); a second activate() of the same action inside that window runs
// it. Env TAOS_GREETER_POWER_CMD replaces `systemctl` (tests, demo mode): it is
// run with the verb (suspend, reboot or poweroff) as its only argument.
class PowerActions : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString armed READ armed NOTIFY armedChanged)
public:
    explicit PowerActions(QObject *parent = nullptr);
    QString armed() const { return m_armed; }
    void setCommand(const QString &program) { m_program = program; }
    void setConfirmMs(int ms) { m_timer.setInterval(ms); }
    static QString verbFor(const QString &action);  // sleep|restart|shutdown -> verb, "" if unknown
    Q_INVOKABLE void activate(const QString &action);
    Q_INVOKABLE void disarm();
signals:
    void armedChanged();
    void ran(const QString &verb, bool started);

private:
    QString m_armed;
    QString m_program;
    QTimer m_timer;
};
