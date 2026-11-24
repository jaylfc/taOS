#include "poweractions.h"

#include <QProcess>

PowerActions::PowerActions(QObject *parent) : QObject(parent) {
    m_program = qEnvironmentVariable("TAOS_GREETER_POWER_CMD", QStringLiteral("systemctl"));
    m_timer.setSingleShot(true);
    m_timer.setInterval(4000);
    connect(&m_timer, &QTimer::timeout, this, &PowerActions::disarm);
}

QString PowerActions::verbFor(const QString &a) {
    if (a == QLatin1String("sleep")) return QStringLiteral("suspend");
    if (a == QLatin1String("restart")) return QStringLiteral("reboot");
    if (a == QLatin1String("shutdown")) return QStringLiteral("poweroff");
    return QString();
}

void PowerActions::disarm() {
    m_timer.stop();
    if (!m_armed.isEmpty()) {
        m_armed.clear();
        emit armedChanged();
    }
}

void PowerActions::activate(const QString &action) {
    const QString verb = verbFor(action);
    if (verb.isEmpty()) return;
    if (m_armed == action) {
        disarm();
        const bool ok = QProcess::startDetached(m_program, {verb});
        emit ran(verb, ok);
        return;
    }
    m_armed = action;
    emit armedChanged();
    m_timer.start();
}
