#include <QtTest>
#include "network.h"

static void mk(const QString &root, const QString &iface, const QString &oper, bool wireless, bool physical = true,
               const QString &carrier = QString()) {
    const QString base = root + "/sys/class/net/" + iface;
    QDir().mkpath(base);
    if (physical) QDir().mkpath(base + "/device");
    if (wireless) QDir().mkpath(base + "/wireless");
    QFile f(base + "/operstate");
    f.open(QIODevice::WriteOnly);
    f.write(oper.toUtf8() + "\n");
    if (!carrier.isEmpty()) {
        QFile c(base + "/carrier");
        c.open(QIODevice::WriteOnly);
        c.write(carrier.toUtf8() + "\n");
    }
}

class TestNetwork : public QObject {
    Q_OBJECT
private slots:
    void emptyTreeIsOffline() {
        QTemporaryDir d;
        QCOMPARE(Network::read(d.path()).kind, Network::Offline);
    }
    void wiredUp() {
        QTemporaryDir d;
        mk(d.path(), "lo", "unknown", false, false);
        mk(d.path(), "enp3s0", "up", false);
        const auto s = Network::read(d.path());
        QCOMPARE(s.kind, Network::Wired);
        QCOMPARE(s.iface, QStringLiteral("enp3s0"));
    }
    void wifiByWirelessDir() {
        QTemporaryDir d;
        mk(d.path(), "wlan0", "up", true);
        QCOMPARE(Network::read(d.path()).kind, Network::Wifi);
    }
    void wifiByProcNetWireless() {
        QTemporaryDir d;
        mk(d.path(), "wlp2s0", "up", false);
        QDir().mkpath(d.path() + "/proc/net");
        QFile f(d.path() + "/proc/net/wireless");
        f.open(QIODevice::WriteOnly);
        f.write("Inter-| sta-|   Quality        |   Discarded packets\n face | tus | link level noise |  nwid\n wlp2s0: 0000   60.  -50.  -256.  0 0 0 0 0 0\n");
        f.close();
        QCOMPARE(Network::read(d.path()).kind, Network::Wifi);
    }
    void downIsOffline() {
        QTemporaryDir d;
        mk(d.path(), "enp3s0", "down", false);
        mk(d.path(), "wlan0", "dormant", true);
        QCOMPARE(Network::read(d.path()).kind, Network::Offline);
    }
    void wiredPreferredOverWifi() {
        QTemporaryDir d;
        mk(d.path(), "wlan0", "up", true);
        mk(d.path(), "eth0", "up", false);
        QCOMPARE(Network::read(d.path()).kind, Network::Wired);
    }
    void virtualLinksIgnored() {
        QTemporaryDir d;
        mk(d.path(), "docker0", "up", false, false);
        mk(d.path(), "veth1234", "up", false, false);
        QCOMPARE(Network::read(d.path()).kind, Network::Offline);
    }
    void unknownOperstateNeedsCarrier() {
        QTemporaryDir d;
        mk(d.path(), "eth0", "unknown", false, true, "0");
        QCOMPARE(Network::read(d.path()).kind, Network::Offline);
        mk(d.path(), "eth0", "unknown", false, true, "1");
        QCOMPARE(Network::read(d.path()).kind, Network::Wired);
    }
    void kindNames() {
        QCOMPARE(Network::kindName(Network::Wired), QStringLiteral("wired"));
        QCOMPARE(Network::kindName(Network::Wifi), QStringLiteral("wifi"));
        QCOMPARE(Network::kindName(Network::Offline), QStringLiteral("offline"));
    }
};
QTEST_APPLESS_MAIN(TestNetwork)
#include "test_network.moc"
