#include "battery.h"

#include <QDir>
#include <QFile>

static QString readFirstLine(const QString &path) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) return {};
    return QString::fromUtf8(f.readLine()).trimmed();
}

Battery::Reading Battery::read(const QString &root) {
    Reading r;
    const QDir dir(root + QStringLiteral("/sys/class/power_supply"));
    const QStringList names = dir.entryList({QStringLiteral("BAT*")}, QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name);
    for (const QString &n : names) {
        const QString base = dir.filePath(n);
        const QString type = readFirstLine(base + QStringLiteral("/type"));
        if (!type.isEmpty() && type.compare(QLatin1String("Battery"), Qt::CaseInsensitive) != 0) continue;
        bool ok = false;
        int cap = readFirstLine(base + QStringLiteral("/capacity")).toInt(&ok);
        if (!ok) continue;
        r.present = true;
        r.percent = qBound(0, cap, 100);
        r.status = readFirstLine(base + QStringLiteral("/status"));
        r.charging = r.status.compare(QLatin1String("Charging"), Qt::CaseInsensitive) == 0;
        break;
    }
    return r;
}

Battery::Battery(const QString &root, QObject *parent) : QObject(parent), m_root(root) {
    m_timer.setInterval(30000);
    m_timer.setTimerType(Qt::VeryCoarseTimer);
    connect(&m_timer, &QTimer::timeout, this, &Battery::refresh);
    m_r = read(m_root);
    // No battery now: still re-check slowly (a dock or hot-plug battery), but cheaply.
    m_timer.start();
}

QString Battery::description() const {
    if (!m_r.present) return {};
    return m_r.charging ? tr("Battery %1 percent, charging").arg(m_r.percent)
                        : tr("Battery %1 percent").arg(m_r.percent);
}

void Battery::refresh() {
    const Reading r = read(m_root);
    if (!(r == m_r)) {
        m_r = r;
        emit changed();
    }
}
