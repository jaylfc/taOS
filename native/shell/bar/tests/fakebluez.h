#pragma once
// A scriptable BlueZ for tests and demos (subset of the real interfaces).
#include <QDBusObjectPath>
#include <QDBusPendingCallWatcher>
#include <QtTest>

#include "fakebus.h"

namespace fakebz {
const QString BZ = QStringLiteral("org.bluez");
const QString AD = BZ + QStringLiteral(".Adapter1");
const QString DV = BZ + QStringLiteral(".Device1");
const QString BAT = BZ + QStringLiteral(".Battery1");
const QString AM = BZ + QStringLiteral(".AgentManager1");
const QString HCI = QStringLiteral("/org/bluez/hci0");
}  // namespace fakebz
using namespace fakebz;

class FakeBluez : public FakeTree {
public:
    QString agentSender, agentPath;
    uint passkey = 123456;
    bool pairShouldFail = false;

    explicit FakeBluez(const QDBusConnection &bus, bool withAdapter = true) : FakeTree(bus) {
        if (withAdapter) objs[HCI][AD] = {{"Powered", true}, {"Discovering", false}, {"Alias", "taos"}};
        methods[AM + ".RegisterAgent"] = [this](const QDBusMessage &m) {
            agentSender = m.service();
            agentPath = m.arguments().value(0).value<QDBusObjectPath>().path();
            reply(m);
        };
        methods[AM + ".RequestDefaultAgent"] = [this](const QDBusMessage &m) { reply(m); };
        methods[AM + ".UnregisterAgent"] = [this](const QDBusMessage &m) { reply(m); };
        methods[AD + ".StartDiscovery"] = [this](const QDBusMessage &m) { setProp(HCI, AD, "Discovering", true); reply(m); };
        methods[AD + ".StopDiscovery"] = [this](const QDBusMessage &m) { setProp(HCI, AD, "Discovering", false); reply(m); };
        methods[AD + ".RemoveDevice"] = [this](const QDBusMessage &m) {
            const QString p = m.arguments().value(0).value<QDBusObjectPath>().path();
            objs.remove(p);
            emitSignal("/", "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved", {opath(p), QVariant(QStringList{DV})});
            reply(m);
        };
        methods[DV + ".Connect"] = [this](const QDBusMessage &m) { setProp(m.path(), DV, "Connected", true); reply(m); };
        methods[DV + ".Disconnect"] = [this](const QDBusMessage &m) { setProp(m.path(), DV, "Connected", false); reply(m); };
        methods[DV + ".Pair"] = [this](const QDBusMessage &m) {
            // Like bluetoothd: ask the caller's agent to confirm the code, then finish.
            QDBusMessage ask = QDBusMessage::createMethodCall(agentSender, agentPath, "org.bluez.Agent1", "RequestConfirmation");
            ask.setArguments({opath(m.path()), passkey});
            auto *w = new QDBusPendingCallWatcher(this->bus().asyncCall(ask, 30000), this);
            connect(w, &QDBusPendingCallWatcher::finished, this, [this, m, w] {
                const bool ok = w->reply().type() != QDBusMessage::ErrorMessage;
                w->deleteLater();
                if (!ok || pairShouldFail) { error(m, "org.bluez.Error.AuthenticationRejected"); return; }
                setProp(m.path(), DV, "Paired", true);
                reply(m);
            });
        };
    }
    void addDevice(const QString &id, const QString &name, bool paired, bool connected, int rssi = -60, int battery = -1, bool named = true) {
        const QString p = HCI + "/dev_" + id;
        QVariantMap d{{"Address", "AA:BB:CC:00:00:" + id}, {"Alias", name}, {"Paired", paired}, {"Connected", connected},
                      {"Trusted", paired}, {"Adapter", opath(HCI)}, {"RSSI", short(rssi)}};
        if (named) d["Name"] = name;
        objs[p][DV] = d;
        if (battery >= 0) objs[p][BAT] = {{"Percentage", uchar(battery)}};
    }
    QVariant added(const QString &id) { return QVariant::fromValue(objs[HCI + "/dev_" + id]); }
};

