// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QDBusObjectPath>
#include <QDBusPendingCallWatcher>
#include <QtTest>

#include "bluezclient.h"

#include "fakebluez.h"

class TestBluez : public QObject {
    Q_OBJECT
    PrivateBus bus;
    QDBusConnection clientBus{QStringLiteral("none")};
    std::unique_ptr<FakeBluez> bz;
    std::unique_ptr<BluezClient> client;
    int n = 0;

    void startClient() {
        client.reset(new BluezClient(clientBus));
        client->start();
    }
    static QStringList names(QAbstractItemModel *m) {
        QStringList l;
        for (int i = 0; i < m->rowCount(); ++i) l << m->index(i, 0).data(BtModel::NameRole).toString();
        return l;
    }
    void makeFake(bool adapter = true) {
        auto fake = bus.connect(QStringLiteral("fake%1").arg(n));
        bz.reset(new FakeBluez(fake, adapter));
        QVERIFY(fake.registerService(BZ));
    }
private slots:
    void initTestCase() { QVERIFY2(bus.start(), "dbus-daemon"); }
    void init() { ++n; clientBus = bus.connect(QStringLiteral("client%1").arg(n)); }
    void cleanup() {
        client.reset();
        if (bz) bz->bus().unregisterService(BZ);
        bz.reset();
    }

    void missingBackendSaysSo() {
        startClient();
        QTRY_VERIFY(!client->message().isEmpty());
        QVERIFY(!client->available());
        QCOMPARE(client->message(), QStringLiteral("Bluetooth is not available on this computer"));
        QVERIFY(!client->switchVisible());
    }
    void noAdapter() {
        makeFake(false);
        startClient();
        QTRY_VERIFY(client->available());
        QCOMPARE(client->message(), QStringLiteral("This computer has no Bluetooth adapter"));
    }
    void listsPairedAndNearby() {
        makeFake();
        bz->addDevice("01", "Keyboard", true, false);
        bz->addDevice("02", "Headphones", true, true, -50, 80);
        bz->addDevice("03", "Phone", false, false, -45);
        bz->addDevice("04", "Speaker", false, false, -70);
        bz->addDevice("05", "00-11-22", false, false, -30, -1, false);  // never gave a name: not worth listing
        startClient();
        QTRY_COMPARE(client->paired()->rowCount(), 2);
        QCOMPARE(names(client->paired()), (QStringList{"Headphones", "Keyboard"}));  // connected first
        QCOMPARE(names(client->nearby()), (QStringList{"Phone", "Speaker"}));        // by signal
        QCOMPARE(client->paired()->index(0, 0).data(BtModel::BatteryRole).toInt(), 80);
        QCOMPARE(client->paired()->index(1, 0).data(BtModel::BatteryRole).toInt(), -1);
        QCOMPARE(client->paired()->index(0, 0).data(BtModel::DescriptionRole).toString(),
                 QStringLiteral("Headphones, connected, battery 80 percent"));
        QVERIFY(client->message().isEmpty());
        QCOMPARE(client->pairedCount(), 2);
    }
    void powerSwitch() {
        makeFake();
        startClient();
        QTRY_VERIFY(client->switchVisible());
        client->setPowered(false);
        QTRY_VERIFY(!client->powered());
        QCOMPARE(client->message(), QStringLiteral("Bluetooth is off"));
        QVERIFY(bz->called("Properties.Set org.bluez.Adapter1.Powered"));
    }
    void discoveryAndAgentOnlyWhileOpen() {
        makeFake();
        startClient();
        QTRY_VERIFY(client->switchVisible());
        client->setActive(true);
        QTRY_VERIFY(client->discovering());
        QVERIFY(bz->called(AM + ".RegisterAgent"));
        QTRY_VERIFY(bz->called(AM + ".RequestDefaultAgent"));
        QCOMPARE(bz->lastArgs[AM + ".RegisterAgent"].value(1).toString(), QStringLiteral("KeyboardDisplay"));
        client->setActive(false);
        QTRY_VERIFY(!client->discovering());
        QTRY_VERIFY(bz->called(AM + ".UnregisterAgent"));
    }
    void pairWithPasskeyConfirm() {
        makeFake();
        bz->addDevice("03", "Phone", false, false, -45);
        startClient();
        QTRY_COMPARE(client->nearby()->rowCount(), 1);
        client->setActive(true);
        QTRY_VERIFY(!bz->agentPath.isEmpty());
        QSignalSpy ann(client.get(), &BluezClient::announcement);
        const QString path = client->nearby()->index(0, 0).data(BtModel::PathRole).toString();
        client->pair(path);
        QTRY_COMPARE(client->requestKind(), int(BluezAgent::Confirm));
        QCOMPARE(client->requestCode(), QStringLiteral("123456"));
        QCOMPARE(client->requestDevice(), QStringLiteral("Phone"));
        client->respond(true);
        QTRY_COMPARE(client->paired()->rowCount(), 1);
        QCOMPARE(client->requestKind(), 0);
        QTRY_VERIFY(bz->called(DV + ".Connect"));
        QVERIFY(bz->called("Properties.Set org.bluez.Device1.Trusted"));
        QTRY_VERIFY(client->paired()->index(0, 0).data(BtModel::ConnectedRole).toBool());
        QStringList said;
        for (const auto &a : ann) said << a.at(0).toString();
        QVERIFY2(said.contains(QString::fromUtf8("Paired with “Phone”")), qPrintable(said.join("|")));
        QVERIFY(client->errorText().isEmpty());
    }
    void pairRefusedIsExplained() {
        makeFake();
        bz->addDevice("03", "Phone", false, false);
        startClient();
        QTRY_COMPARE(client->nearby()->rowCount(), 1);
        client->setActive(true);
        QTRY_VERIFY(!bz->agentPath.isEmpty());
        client->pair(client->nearby()->index(0, 0).data(BtModel::PathRole).toString());
        QTRY_COMPARE(client->requestKind(), int(BluezAgent::Confirm));
        client->respond(false);  // the person says the codes do not match
        QTRY_VERIFY(!client->errorText().isEmpty());
        QCOMPARE(client->errorText(), QString::fromUtf8("“Phone” did not accept the pairing."));
        QCOMPARE(client->paired()->rowCount(), 0);
        QVERIFY(!bz->called(DV + ".Connect"));
    }
    void requestWhileClosedAsksForAttention() {
        makeFake();
        bz->addDevice("03", "Phone", false, false);
        startClient();
        QTRY_VERIFY(client->switchVisible());
        client->setActive(true);
        QTRY_VERIFY(!bz->agentPath.isEmpty());
        client->setActive(false);  // closed again, agent kept by the fake: simulate a stray request
        QSignalSpy att(client.get(), &BluezClient::attention);
        QDBusMessage ask = QDBusMessage::createMethodCall(clientBus.baseService(), "/org/taos/bar/Agent", "org.bluez.Agent1", "RequestAuthorization");
        ask.setArguments({opath(HCI + "/dev_03")});
        bz->bus().asyncCall(ask, 5000);
        QTRY_COMPARE(att.count(), 1);
        QCOMPARE(client->requestKind(), int(BluezAgent::Authorize));
        client->respond(false);
    }
    void connectDisconnectForget() {
        makeFake();
        bz->addDevice("01", "Keyboard", true, false);
        startClient();
        QTRY_COMPARE(client->paired()->rowCount(), 1);
        const QString p = client->paired()->index(0, 0).data(BtModel::PathRole).toString();
        client->connectDevice(p);
        QTRY_VERIFY(client->paired()->index(0, 0).data(BtModel::ConnectedRole).toBool());
        client->disconnectDevice(p);
        QTRY_VERIFY(!client->paired()->index(0, 0).data(BtModel::ConnectedRole).toBool());
        client->forget(p);
        QTRY_COMPARE(client->paired()->rowCount(), 0);
    }
    void devicesAppearAndDisappearLive() {
        makeFake();
        startClient();
        QTRY_VERIFY(client->switchVisible());
        bz->addDevice("09", "Mouse", false, false, -40);
        bz->emitSignal("/", "org.freedesktop.DBus.ObjectManager", "InterfacesAdded", {opath(HCI + "/dev_09"), bz->added("09")});
        QTRY_COMPARE(names(client->nearby()), (QStringList{"Mouse"}));
        bz->emitSignal("/", "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved", {opath(HCI + "/dev_09"), QVariant(QStringList{DV})});
        QTRY_COMPARE(client->nearby()->rowCount(), 0);
    }
    void batteryUpdatesLive() {
        makeFake();
        bz->addDevice("02", "Headphones", true, true, -50, 80);
        startClient();
        QTRY_COMPARE(client->paired()->rowCount(), 1);
        bz->setProp(HCI + "/dev_02", BAT, "Percentage", QVariant::fromValue(uchar(35)));
        QTRY_COMPARE(client->paired()->index(0, 0).data(BtModel::BatteryRole).toInt(), 35);
    }
};
QTEST_MAIN(TestBluez)
#include "test_bluezclient.moc"
