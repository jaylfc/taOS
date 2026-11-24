// SPDX-License-Identifier: AGPL-3.0-or-later
#include "network.h"

#include "nmclient.h"

#include <QDir>
#include <QLocale>
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

Network::Ethernet Network::readEthernet(const QString &root) {
    const QDir dir(root + QStringLiteral("/sys/class/net"));
    Ethernet best;
    for (const QString &n : dir.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name)) {
        if (n == QLatin1String("lo")) continue;
        const QString base = dir.filePath(n);
        if (QFileInfo(base + QStringLiteral("/wireless")).isDir() || !QFileInfo::exists(base + QStringLiteral("/device"))) continue;
        const QString oper = readFirstLine(base + QStringLiteral("/operstate"));
        const bool up = oper == QLatin1String("up")
                        || (oper == QLatin1String("unknown") && readFirstLine(base + QStringLiteral("/carrier")) == QLatin1String("1"));
        QString text;
        if (up) {
            const int speed = readFirstLine(base + QStringLiteral("/speed")).toInt();
            if (speed >= 1000) text = tr("Connected, %1 Gbit/s").arg(QLocale().toString(speed / 1000.0, 'g', 3));
            else if (speed > 0) text = tr("Connected, %1 Mbit/s").arg(speed);
            else text = tr("Connected");
        } else {
            text = tr("Cable unplugged");
        }
        if (!best.present || up) {
            best = {true, text};
            if (up) break;
        }
    }
    return best;
}

Network::Network(const QString &root, QObject *parent) : QObject(parent), m_root(root) {
    m_state = read(m_root);
    m_eth = readEthernet(m_root);
    m_timer.setInterval(5000);
    m_timer.setTimerType(Qt::VeryCoarseTimer);
    connect(&m_timer, &QTimer::timeout, this, &Network::refresh);
    m_timer.start();
}

QString Network::description() const {
    switch (m_state.kind) {
    case Wired: return tr("Network: wired, connected");
    case Wifi:
        if (!ssid().isEmpty())
            return tr("Network: Wi-Fi, %1, signal %2").arg(ssid(), WifiModel::strengthWord(strength()));
        return tr("Network: Wi-Fi, connected");
    default: return tr("Network: offline");
    }
}

bool Network::nmDriven() const { return m_nm && m_nm->available(); }
int Network::nmStrength() const { return nmDriven() ? m_nm->activeStrength() : 0; }
QString Network::ssid() const { return nmDriven() ? m_nm->activeSsid() : QString(); }
bool Network::ethernetPresent() const { return nmDriven() ? m_nm->ethernetPresent() : m_eth.present; }
QString Network::ethernetText() const { return nmDriven() ? m_nm->ethernetText() : m_eth.text; }

void Network::attachNm(NmClient *nm) {
    m_nm = nm;
    connect(nm, &NmClient::changed, this, &Network::nmChanged);
    nmChanged();
}

void Network::nmChanged() {
    State s = m_state;
    if (nmDriven()) {
        m_timer.stop();  // the daemon tells us; no polling
        const QString k = m_nm->kind();
        s = {k == QLatin1String("wired") ? Wired : k == QLatin1String("wifi") ? Wifi : Offline, QString()};
    } else {
        if (!m_timer.isActive()) m_timer.start();
        s = read(m_root);
    }
    // Signal strength and names change without the kind changing.
    m_state = s;
    emit changed();
}

void Network::refresh() {
    if (nmDriven()) return;
    const State s = read(m_root);
    const Ethernet e = readEthernet(m_root);
    if (!(s == m_state) || e.present != m_eth.present || e.text != m_eth.text) {
        m_state = s;
        m_eth = e;
        emit changed();
    }
}
