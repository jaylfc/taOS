#include "network.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSet>

static QString readFirstLine(const QString &path) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) return {};
    return QString::fromUtf8(f.readLine()).trimmed();
}

static QSet<QString> procWireless(const QString &root) {
    QSet<QString> names;
    QFile f(root + QStringLiteral("/proc/net/wireless"));
    if (!f.open(QIODevice::ReadOnly)) return names;
    int n = 0;
    while (!f.atEnd()) {
        const QString line = QString::fromUtf8(f.readLine());
        if (++n <= 2) continue;  // two header lines
        const int colon = line.indexOf(QLatin1Char(':'));
        if (colon > 0) names.insert(line.left(colon).trimmed());
    }
    return names;
}

QString Network::kindName(Kind k) {
    switch (k) {
    case Wired: return QStringLiteral("wired");
    case Wifi: return QStringLiteral("wifi");
    default: return QStringLiteral("offline");
    }
}

Network::State Network::read(const QString &root) {
    const QDir dir(root + QStringLiteral("/sys/class/net"));
    const QSet<QString> wirelessListed = procWireless(root);
    State wired, wifi;
    const QStringList names = dir.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name);
    for (const QString &n : names) {
        if (n == QLatin1String("lo")) continue;
        const QString base = dir.filePath(n);
        const bool isWifi = QFileInfo(base + QStringLiteral("/wireless")).isDir() || wirelessListed.contains(n);
        // Virtual links (bridges, veth, tunnels) have no `device`; skip them.
        if (!isWifi && !QFileInfo::exists(base + QStringLiteral("/device"))) continue;
        const QString oper = readFirstLine(base + QStringLiteral("/operstate"));
        const bool up = oper == QLatin1String("up")
                        || (oper == QLatin1String("unknown")
                            && readFirstLine(base + QStringLiteral("/carrier")) == QLatin1String("1"));
        if (!up) continue;
        if (isWifi) {
            if (wifi.kind == Offline) wifi = {Wifi, n};
        } else if (wired.kind == Offline) {
            wired = {Wired, n};
        }
    }
    if (wired.kind != Offline) return wired;
    return wifi;  // Offline with empty iface when neither is up
}

Network::Network(const QString &root, QObject *parent) : QObject(parent), m_root(root) {
    m_state = read(m_root);
    m_timer.setInterval(5000);
    m_timer.setTimerType(Qt::VeryCoarseTimer);
    connect(&m_timer, &QTimer::timeout, this, &Network::refresh);
    m_timer.start();
}

QString Network::description() const {
    switch (m_state.kind) {
    case Wired: return tr("Network: wired, connected");
    case Wifi: return tr("Network: Wi-Fi, connected");
    default: return tr("Network: offline");
    }
}

void Network::refresh() {
    const State s = read(m_root);
    if (!(s == m_state)) {
        m_state = s;
        emit changed();
    }
}
