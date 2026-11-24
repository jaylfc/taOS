// SPDX-License-Identifier: AGPL-3.0-or-later
#include "clock.h"

Clock::Clock(QObject *parent) : QObject(parent) {
    m_timer.setSingleShot(true);
    m_timer.setTimerType(Qt::PreciseTimer);
    connect(&m_timer, &QTimer::timeout, this, &Clock::refresh);
    refresh();
}

void Clock::setShowSeconds(bool on) {
    if (on == m_seconds) return;
    m_seconds = on;
    emit settingsChanged();
    refresh();
}

void Clock::setUse24Hour(bool on) {
    if (on == m_24h) return;
    m_24h = on;
    emit settingsChanged();
    refresh();
}

QString Clock::formatTime(const QDateTime &dt, const QLocale &loc, bool h24, bool seconds) {
    QString fmt = h24 ? QStringLiteral("HH:mm") : QStringLiteral("h:mm");
    if (seconds) fmt += QStringLiteral(":ss");
    if (!h24) fmt += QStringLiteral(" AP");
    return loc.toString(dt, fmt);
}

QString Clock::formatDate(const QDateTime &dt, const QLocale &loc) {
    return loc.toString(dt, QStringLiteral("ddd d MMM"));
}

int Clock::msToNextTick(const QDateTime &now, bool seconds) {
    const int into = seconds ? now.time().msec()
                             : (now.time().second() * 1000 + now.time().msec());
    const int period = seconds ? 1000 : 60000;
    return qMax(1, period - into);
}

void Clock::refresh() {
    const QDateTime now = QDateTime::currentDateTime();
    const QLocale loc;
    const QString t = formatTime(now, loc, m_24h, m_seconds);
    const QString d = formatDate(now, loc);
    if (t != m_time || d != m_date) {
        m_time = t;
        m_date = d;
        emit changed();
    }
    m_timer.start(msToNextTick(now, m_seconds));
}
