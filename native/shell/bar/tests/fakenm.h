#pragma once
// A scriptable NetworkManager for tests and demos (subset of the real interfaces).
#include <QDBusObjectPath>
#include <QtTest>

#include "fakebus.h"

namespace fakenm {
const QString NM = QStringLiteral("org.freedesktop.NetworkManager");
const QString ROOT = QStringLiteral("/org/freedesktop/NetworkManager");
const QString DEV = NM + QStringLiteral(".Device");
const QString WL = DEV + QStringLiteral(".Wireless");
const QString WD = DEV + QStringLiteral(".Wired");
const QString APIF = NM + QStringLiteral(".AccessPoint");
const QString WIFIDEV = ROOT + QStringLiteral("/Devices/2");
const QString ETHDEV = ROOT + QStringLiteral("/Devices/1");
const QString SETTINGS = ROOT + QStringLiteral("/Settings");
const QString SECRET = QStringLiteral("hunter22-secret");

QVariant paths(const QStringList &l) {
    QList<QDBusObjectPath> out;
    for (const QString &p : l) out << QDBusObjectPath(p);
    return QVariant::fromValue(out);
}
}  // namespace fakenm
using namespace fakenm;

class FakeNm : public FakeTree {
public:
    int apSeq = 0;
    int connSeq = 0;
    QStringList devices;
    bool denyAdd = false;
    DBusSettings lastAdd;

    explicit FakeNm(const QDBusConnection &bus) : FakeTree(bus) {
        objs[ROOT][NM] = {{"WirelessEnabled", true}, {"WirelessHardwareEnabled", true}, {"State", 70u}};
        objs[SETTINGS][NM + ".Settings"] = {};
        methods[NM + ".GetDevices"] = [this](const QDBusMessage &m) { reply(m, {paths(devices)}); };
        methods[WL + ".GetAllAccessPoints"] = [this](const QDBusMessage &m) {
            QStringList l;
            for (auto it = objs.cbegin(); it != objs.cend(); ++it)
                if (it.value().contains(APIF)) l << it.key();
            reply(m, {paths(l)});
        };
        methods[WL + ".RequestScan"] = [this](const QDBusMessage &m) { reply(m); };
        methods[NM + ".Settings.ListConnections"] = [this](const QDBusMessage &m) {
            QStringList l;
            for (auto it = objs.cbegin(); it != objs.cend(); ++it)
                if (it.value().contains(NM + ".Settings.Connection")) l << it.key();
            reply(m, {paths(l)});
        };
        methods[NM + ".Settings.Connection.GetSettings"] = [this](const QDBusMessage &m) {
            reply(m, {QVariant::fromValue(connSettings.value(m.path()))});
        };
        methods[NM + ".Settings.Connection.Delete"] = [this](const QDBusMessage &m) {
            objs.remove(m.path());
            connSettings.remove(m.path());
            emitSignal(SETTINGS, NM + ".Settings", "ConnectionRemoved", {opath(m.path())});
            reply(m);
        };
        methods[NM + ".AddAndActivateConnection"] = [this](const QDBusMessage &m) {
            if (denyAdd) { error(m, NM + ".PermissionDenied"); return; }
            lastAdd = dbusutil::toSettings(m.arguments().value(0));
            const QString conn = SETTINGS + "/" + QString::number(100 + ++connSeq);
            objs[conn][NM + ".Settings.Connection"] = {};
            connSettings[conn] = lastAdd;
            reply(m, {opath(conn), opath(ROOT + "/ActiveConnection/1")});
            const QString psk = lastAdd.value("802-11-wireless-security").value("psk").toString();
            const bool secured = lastAdd.contains("802-11-wireless-security");
            QTimer::singleShot(0, this, [this, psk, secured] {
                const bool ok = !secured || psk == SECRET;
                emitSignal(WIFIDEV, DEV, "StateChanged", {ok ? 100u : 120u, 40u, ok ? 0u : 7u});
                setProp(WIFIDEV, DEV, "State", ok ? 100u : 120u);
            });
        };
        methods[NM + ".ActivateConnection"] = [this](const QDBusMessage &m) {
            reply(m, {opath(ROOT + "/ActiveConnection/2")});
            QTimer::singleShot(0, this, [this] {
                emitSignal(WIFIDEV, DEV, "StateChanged", {100u, 40u, 0u});
                setProp(WIFIDEV, DEV, "State", 100u);
            });
        };
        methods[DEV + ".Disconnect"] = [this](const QDBusMessage &m) { reply(m); };
    }
    QMap<QString, DBusSettings> connSettings;

    void addWifi(uint state = 30) {
        devices << WIFIDEV;
        objs[WIFIDEV][DEV] = {{"DeviceType", 2u}, {"Interface", "wlan0"}, {"State", state}};
        objs[WIFIDEV][WL] = {{"ActiveAccessPoint", opath("/")}};
    }
    void addEthernet(uint state, bool carrier, uint speed) {
        devices << ETHDEV;
        objs[ETHDEV][DEV] = {{"DeviceType", 1u}, {"Interface", "enp3s0"}, {"State", state}};
        objs[ETHDEV][WD] = {{"Speed", speed}, {"Carrier", carrier}};
    }
    QString addAp(const QString &ssid, int strength, bool secured) {
        const QString p = ROOT + "/AccessPoint/" + QString::number(++apSeq);
        objs[p][APIF] = {{"Ssid", ssid.toUtf8()}, {"Strength", uchar(strength)}, {"Flags", secured ? 1u : 0u},
                         {"WpaFlags", 0u}, {"RsnFlags", secured ? 0x100u : 0u}};
        return p;
    }
    void addSaved(const QString &ssid) {
        const QString conn = SETTINGS + "/" + QString::number(++connSeq);
        objs[conn][NM + ".Settings.Connection"] = {};
        DBusSettings s;
        s["connection"] = {{"type", "802-11-wireless"}, {"id", ssid}};
        s["802-11-wireless"] = {{"ssid", ssid.toUtf8()}};
        connSettings[conn] = s;
    }
};

