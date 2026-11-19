#include <QDBusObjectPath>
#include <QtTest>

#include "fakebus.h"
#include "nmclient.h"

namespace {
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

QVariant opath(const QString &p) { return QVariant::fromValue(QDBusObjectPath(p)); }
QVariant paths(const QStringList &l) {
    QList<QDBusObjectPath> out;
    for (const QString &p : l) out << QDBusObjectPath(p);
    return QVariant::fromValue(out);
}
}  // namespace

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

class TestNmClient : public QObject {
    Q_OBJECT
    PrivateBus bus;
    QDBusConnection clientBus{QStringLiteral("none")};
    std::unique_ptr<FakeNm> nm;
    std::unique_ptr<NmClient> client;
    int n = 0;

    void startClient() {
        client.reset(new NmClient(clientBus));
        client->start();
    }
    QStringList ssids() const {
        QStringList l;
        for (const WifiEntry &e : client->networks()->rows()) l << e.ssid;
        return l;
    }
    const WifiEntry *row(const QString &s) const {
        for (const WifiEntry &e : client->networks()->rows()) if (e.ssid == s) return &e;
        return nullptr;
    }
private slots:
    void initTestCase() {
        QVERIFY2(bus.start(), "dbus-daemon");
    }
    void init() {
        // A new connection pair per test so the fake and the client start clean.
        ++n;
        clientBus = bus.connect(QStringLiteral("client%1").arg(n));
        auto fake = bus.connect(QStringLiteral("fake%1").arg(n));
        nm.reset(new FakeNm(fake));
        QVERIFY(fake.registerService(NM));
    }
    void cleanup() {
        client.reset();
        if (nm) { nm->bus().unregisterService(NM); }
        nm.reset();
    }

    void missingBackendSaysSo() {
        nm->bus().unregisterService(NM);
        startClient();
        QTRY_VERIFY(!client->wifiMessage().isEmpty());
        QVERIFY(!client->available());
        QCOMPARE(client->wifiMessage(), QStringLiteral("Wi-Fi is not available on this computer"));
        QVERIFY(!client->switchVisible());
        QCOMPARE(client->kind(), QString());
    }
    void noWifiAdapter() {
        nm->addEthernet(20, false, 0);
        startClient();
        QTRY_VERIFY(client->ethernetPresent());
        QCOMPARE(client->wifiMessage(), QStringLiteral("This computer has no Wi-Fi adapter"));
        QCOMPARE(client->ethernetText(), QStringLiteral("Cable unplugged"));
    }
    void ethernetConnectedShowsSpeed() {
        nm->addEthernet(100, true, 1000);
        startClient();
        QTRY_COMPARE(client->ethernetText(), QStringLiteral("Connected, 1 Gbit/s"));
        QCOMPARE(client->kind(), QStringLiteral("wired"));
    }
    void listSortedAndCurrentMarked() {
        nm->addWifi(100);
        const QString home = nm->addAp("Home", 62, true);
        nm->addAp("Neighbour", 90, true);
        nm->addAp("Neighbour", 30, true);  // second access point of the same network
        nm->addAp("Cafe", 45, false);
        nm->addAp("Weak", 8, false);
        nm->addAp("", 70, false);  // hidden network: not listed
        nm->objs[WIFIDEV][WL]["ActiveAccessPoint"] = opath(home);
        startClient();
        QTRY_COMPARE(client->networks()->rowCount(), 4);
        QCOMPARE(ssids(), (QStringList{"Home", "Neighbour", "Cafe", "Weak"}));  // current first, then by signal
        QVERIFY(row("Home")->active);
        QVERIFY(!row("Neighbour")->active);
        QCOMPARE(row("Neighbour")->strength, 90);
        QVERIFY(row("Home")->secured);
        QVERIFY(!row("Cafe")->secured);
        QCOMPARE(client->activeSsid(), QStringLiteral("Home"));
        QCOMPARE(client->kind(), QStringLiteral("wifi"));
        QCOMPARE(client->networks()->index(0).data(WifiModel::DescriptionRole).toString(),
                 QStringLiteral("Home, connected, signal fair, secured"));
    }
    void listUpdatesFromSignals() {
        nm->addWifi();
        nm->addAp("Cafe", 45, false);
        startClient();
        QTRY_COMPARE(client->networks()->rowCount(), 1);
        const QString p = nm->addAp("Library", 80, false);
        nm->emitSignal(WIFIDEV, WL, "AccessPointAdded", {opath(p)});
        QTRY_COMPARE(ssids(), (QStringList{"Library", "Cafe"}));
        nm->setProp(p, APIF, "Strength", QVariant::fromValue(uchar(5)));
        QTRY_COMPARE(row("Library")->strength, 5);
        nm->emitSignal(WIFIDEV, WL, "AccessPointRemoved", {opath(p)});
        QTRY_COMPARE(ssids(), (QStringList{"Cafe"}));
    }
    void wifiSwitch() {
        nm->addWifi();
        nm->addAp("Cafe", 45, false);
        startClient();
        QTRY_COMPARE(client->networks()->rowCount(), 1);
        QVERIFY(client->switchVisible());
        QSignalSpy ann(client.get(), &NmClient::announcement);
        client->setWifiEnabled(false);
        QTRY_VERIFY(!client->wifiEnabled());
        QCOMPARE(client->wifiMessage(), QStringLiteral("Wi-Fi is off"));
        QTRY_COMPARE(client->networks()->rowCount(), 0);
        QVERIFY(ann.count() >= 1);
        QCOMPARE(ann.last().at(0).toString(), QStringLiteral("Wi-Fi turned off"));
        client->setWifiEnabled(true);
        QTRY_VERIFY(client->wifiEnabled());
        QVERIFY(client->wifiMessage().isEmpty());
    }
    void securedConnectAsksThenSendsSecretToDaemon() {
        nm->addWifi();
        nm->addAp("Neighbour", 90, true);
        startClient();
        QTRY_COMPARE(client->networks()->rowCount(), 1);
        QSignalSpy ann(client.get(), &NmClient::announcement);
        client->connectTo("Neighbour");
        QCOMPARE(client->passwordSsid(), QStringLiteral("Neighbour"));  // asks first
        QVERIFY(!nm->called(NM + ".AddAndActivateConnection"));
        client->submitPassword("Neighbour", SECRET);
        QTRY_COMPARE(client->kind(), QStringLiteral("wifi") );
        QVERIFY(client->passwordSsid().isEmpty());
        const QVariantMap sec = nm->lastAdd.value("802-11-wireless-security");
        QCOMPARE(sec.value("key-mgmt").toString(), QStringLiteral("wpa-psk"));
        QCOMPARE(sec.value("psk").toString(), SECRET);
        QCOMPARE(nm->lastAdd.value("802-11-wireless").value("ssid").toByteArray(), QByteArray("Neighbour"));
        QTRY_VERIFY(ann.count() >= 2);
        QCOMPARE(ann.last().at(0).toString(), QString::fromUtf8("Connected to “Neighbour”"));
        QVERIFY(client->errorText().isEmpty());
    }
    void wrongPasswordIsShownInPlainWords() {
        nm->addWifi();
        nm->addAp("Neighbour", 90, true);
        startClient();
        QTRY_COMPARE(client->networks()->rowCount(), 1);
        client->connectTo("Neighbour");
        client->submitPassword("Neighbour", "not-the-password");
        QTRY_VERIFY(!client->errorText().isEmpty());
        QCOMPARE(client->errorText(), QString::fromUtf8("The password for “Neighbour” was not accepted."));
        QVERIFY(!client->errorText().contains("not-the-password"));
        QCOMPARE(client->passwordSsid(), QStringLiteral("Neighbour"));  // asks again
        QVERIFY(client->passwordRetry());
        QTRY_VERIFY(nm->called(NM + ".Settings.Connection.Delete"));  // the failed profile is dropped
        QCOMPARE(client->kind(), QStringLiteral("offline"));
    }
    void openNetworkJoinsWithoutAsking() {
        nm->addWifi();
        nm->addAp("Cafe", 45, false);
        startClient();
        QTRY_COMPARE(client->networks()->rowCount(), 1);
        client->connectTo("Cafe");
        QTRY_COMPARE(client->kind(), QStringLiteral("wifi"));
        QVERIFY(client->passwordSsid().isEmpty());
        QVERIFY(!nm->lastAdd.contains("802-11-wireless-security"));
    }
    void savedNetworkActivatesWithoutAsking() {
        nm->addWifi();
        nm->addAp("Home", 70, true);
        nm->addSaved("Home");
        startClient();
        QTRY_VERIFY(client->networks()->rowCount() == 1 && row("Home")->known);
        client->connectTo("Home");
        QTRY_VERIFY(nm->called(NM + ".ActivateConnection"));
        QVERIFY(client->passwordSsid().isEmpty());
        QTRY_COMPARE(client->kind(), QStringLiteral("wifi"));
        client->forget("Home");
        QTRY_VERIFY(nm->called(NM + ".Settings.Connection.Delete"));
        QTRY_VERIFY(!row("Home")->known);
    }
    void notAllowedIsExplained() {
        nm->addWifi();
        nm->addAp("Cafe", 45, false);
        nm->denyAdd = true;
        startClient();
        QTRY_COMPARE(client->networks()->rowCount(), 1);
        client->connectTo("Cafe");
        QTRY_VERIFY(!client->errorText().isEmpty());
        QCOMPARE(client->errorText(), QStringLiteral("This account is not allowed to join Wi-Fi networks on this computer."));
    }
    void hiddenNetwork() {
        nm->addWifi();
        startClient();
        QTRY_VERIFY(client->switchVisible());
        client->connectHidden("Backroom", "pw12345678");
        QTRY_VERIFY(nm->called(NM + ".AddAndActivateConnection"));
        QVERIFY(nm->lastAdd.value("802-11-wireless").value("hidden").toBool());
        QCOMPARE(nm->lastAdd.value("802-11-wireless-security").value("psk").toString(), QStringLiteral("pw12345678"));
    }
    void enterpriseNetworkIsRefusedPolitely() {
        nm->addWifi();
        const QString p = nm->addAp("Corp", 80, true);
        nm->objs[p][APIF]["RsnFlags"] = 0x200u;
        startClient();
        QTRY_COMPARE(client->networks()->rowCount(), 1);
        client->connectTo("Corp");
        QVERIFY(client->errorText().contains("work or school sign-in"));
        QVERIFY(client->passwordSsid().isEmpty());
    }
    void rowsKeepIdentityWhenOnlySignalChanges() {
        nm->addWifi();
        const QString a = nm->addAp("Alpha", 55, false);
        nm->addAp("Beta", 41, false);
        startClient();
        QTRY_COMPARE(client->networks()->rowCount(), 2);
        QSignalSpy reset(client->networks(), &QAbstractItemModel::modelReset);
        QSignalSpy data(client->networks(), &QAbstractItemModel::dataChanged);
        nm->setProp(a, APIF, "Strength", QVariant::fromValue(uchar(58)));  // same ten point bucket: same order
        QTRY_VERIFY(data.count() >= 1);
        QCOMPARE(reset.count(), 0);
    }
};
QTEST_MAIN(TestNmClient)
#include "test_nmclient.moc"
