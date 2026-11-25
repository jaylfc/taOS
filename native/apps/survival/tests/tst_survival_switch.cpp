// SPDX-License-Identifier: AGPL-3.0-or-later
// Moving a radio board between networks (RadioSwitch), driven through the real socket and HTTP paths against stand-in
// engines that answer with the real engines' captured JSON. The stand-ins can neither transmit nor flash: they record
// every request, and these tests assert the ORDER (save, release, flash, reconnect, restore) and what was not done.
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "fakemesh.h"
#include "firmwareclient.h"
#include "meshclient.h"
#include "radioprofile.h"
#include "radioswitch.h"
#include "socketmeshbackend.h"

using namespace Taos::Survival;
using namespace FakeEngines;
using namespace Qt::StringLiterals;

namespace {
const QString kPort = u"/dev/ttyUSB0"_s;
const QString kDevice = u"usb-10c4-ea60-SN1-1-1"_s;

QJsonObject radio(const QString &id, const QString &network, const QString &fw)
{
    return {{u"id"_s, id}, {u"name"_s, u"Heltec test"_s}, {u"transport"_s, network}, {u"link"_s, u"usb"_s}, {u"state"_s, u"connected"_s}, {u"firmware"_s, fw},
            {u"hardware"_s, u"Heltec V3"_s}, {u"port"_s, kPort}, {u"batteryPercent"_s, -1}};
}
QJsonObject mtConfig(const QString &region, const QString &preset, const QString &chName)
{
    return {{u"network"_s, u"meshtastic"_s}, {u"firmware"_s, u"2.7.15"_s}, {u"hardware"_s, u"Heltec V3"_s}, {u"owner"_s, QJsonObject{{u"long_name"_s, u"Jay Heltec"_s}}},
            {u"lora"_s, QJsonObject{{u"region"_s, region}, {u"modem_preset"_s, preset}}},
            {u"channels"_s, QJsonArray{QJsonObject{{u"index"_s, 1}, {u"name"_s, chName}, {u"role"_s, u"SECONDARY"_s}, {u"psk_b64"_s, u"AQID"_s}}}},
            {u"writable"_s, QJsonArray{u"owner.long_name"_s, u"lora.region"_s, u"lora.modem_preset"_s, u"channels.<n>.name"_s}}};
}
QJsonObject mcConfig()
{
    return {{u"network"_s, u"meshcore"_s}, {u"firmware"_s, u"v1.9.1"_s}, {u"hardware"_s, u"Heltec V3"_s}, {u"name"_s, u"Trail Node"_s},
            {u"radio"_s, QJsonObject{{u"freq_mhz"_s, 869.618}, {u"sf"_s, 8}}}, {u"writable"_s, QJsonArray()}};
}
} // namespace

class TstSurvivalSwitch : public QObject
{
    Q_OBJECT
    QString mfx() const { return QStringLiteral(MESH_FIXTURES_DIR) + u"/mesh"_s; }
    QString ffx() const { return QStringLiteral(MESH_FIXTURES_DIR) + u"/firmware"_s; }

    struct Rig
    {
        QTemporaryDir dir;
        std::unique_ptr<FakeMeshEngine> eng;
        std::unique_ptr<MeshClient> client;
        std::unique_ptr<SocketMeshBackend> backend;
        std::unique_ptr<FakeFirmware> fw;
        std::unique_ptr<FirmwareClient> fc;
        std::unique_ptr<RadioProfiles> profiles;
        std::unique_ptr<RadioSwitch> sw;
    };
    std::unique_ptr<Rig> rig(const QJsonObject &startRadio, const QJsonObject &startCfg)
    {
        auto r = std::make_unique<Rig>();
        r->eng = std::make_unique<FakeMeshEngine>(mfx());
        r->eng->receiveOnly = false;
        r->eng->configReadable = true;
        r->eng->configWritable = true;
        r->eng->scripted = true;
        r->eng->radiosJson = QJsonArray{startRadio};
        r->eng->cfgByRadio.insert(startRadio.value(u"id"_s).toString(), startCfg);
        r->eng->serialDevices = QJsonArray{QJsonObject{{u"path"_s, kPort}, {u"vid"_s, 0x10c4}, {u"pid"_s, 0xea60}, {u"product"_s, u"Heltec V3"_s}}};
        const QString sock = r->dir.path() + u"/mesh.sock"_s;
        if (!r->eng->listen(sock))
            return nullptr;
        r->client = std::make_unique<MeshClient>(sock);
        r->backend = std::make_unique<SocketMeshBackend>(r->client.get());
        r->fw = std::make_unique<FakeFirmware>(ffx(), r->dir.path() + u"/fw"_s);
        r->fc = std::make_unique<FirmwareClient>();
        r->fc->setDir(r->dir.path() + u"/fw"_s);
        r->fc->setPort(r->fw->port());
        r->profiles = std::make_unique<RadioProfiles>(r->dir.path() + u"/data"_s);
        r->sw = std::make_unique<RadioSwitch>(r->backend.get(), r->fc.get(), r->profiles.get());
        r->sw->setTimeouts(3000, 15000);
        r->client->start();
        return r;
    }
    SwitchRequest req(const QString &from, const QString &to, const QString &typed = u"Heltec V3"_s)
    {
        SwitchRequest q;
        q.port = kPort;
        q.radioId = from == u"meshtastic"_s ? u"!a1b2c3d4"_s : u"a1b2c3d4e5f6"_s;
        q.deviceId = kDevice;
        q.entryId = to + u":heltec-v3:1.0"_s;
        q.fromNetwork = from;
        q.toNetwork = to;
        q.typedName = typed;
        q.ackUnsigned = true;
        return q;
    }
    static int firstIndex(const QList<QJsonObject> &reqs, const QString &method)
    {
        for (int i = 0; i < reqs.size(); ++i)
            if (reqs[i].value(u"method"_s).toString() == method)
                return i;
        return -1;
    }

private Q_SLOTS:
    void meshtastic_to_meshcore_saves_then_releases_then_flashes_then_reconnects_in_that_order()
    {
        auto r = rig(radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s), mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->backend->radios().size() == 1, 5000);
        QCOMPARE(r->backend->radios().first().hardware, u"Heltec V3"_s);
        QCOMPARE(r->backend->radios().first().port, kPort);
        r->eng->onConnect = [e = r->eng.get()](const QString &) {
            e->radiosJson = QJsonArray{radio(u"a1b2c3d4e5f6"_s, u"meshcore"_s, u"v1.9.1"_s)};
            e->cfgByRadio.insert(u"a1b2c3d4e5f6"_s, mcConfig());
        };
        r->sw->start(req(u"meshtastic"_s, u"meshcore"_s));
        QTRY_VERIFY_WITH_TIMEOUT(!r->sw->running(), 30000);
        QVERIFY2(r->sw->step() == RadioSwitch::Step::Done, qPrintable(r->sw->error()));
        QVERIFY(r->sw->profileSaved());
        // order: the settings were read, then the port let go, only then the flash, and the reconnect came last
        const auto &reqs = r->eng->requests;
        const int cfg = firstIndex(reqs, u"device.config"_s), rel = firstIndex(reqs, u"radios.release"_s), con = firstIndex(reqs, u"radios.connect"_s);
        QVERIFY(cfg >= 0 && rel > cfg && con > rel);
        const auto &fwr = r->fw->requests;
        int run = -1;
        for (int i = 0; i < fwr.size(); ++i)
            if (fwr[i].path == u"/v1/flash/run"_s)
                run = i;
        QVERIFY(run >= 0);
        QCOMPARE(r->fw->count(u"POST"_s, u"/v1/flash/run"_s), 1);
        QCOMPARE(r->eng->released, QStringList{kPort});
        QCOMPARE(r->fw->mints.size(), 1);
        QCOMPARE(r->fw->mints.first().action, u"flash.run"_s);
        QCOMPARE(r->fw->mints.first().subject, kDevice + u"|meshcore:heltec-v3:1.0"_s);
        QCOMPARE(r->fw->last(u"/v1/flash/run"_s).body.value(u"confirm"_s).toString(), u"Heltec V3"_s);
        QCOMPARE(r->fw->last(u"/v1/flash/run"_s).body.value(u"actor"_s).toString(), u"person"_s);
        // the connect did not name a network: the engine's read-only handshake decides it
        QVERIFY(!r->eng->last(u"radios.connect"_s).value(u"params"_s).toObject().contains(u"network"_s));
        // nothing was sent over the air and no setting was written
        QVERIFY(r->eng->forbiddenSeen({u"radios.connect"_s, u"radios.release"_s}).isEmpty());
        // the old network's settings are on disk, private to the owner
        const QVariantMap saved = r->profiles->load(kDevice, u"meshtastic"_s);
        QCOMPARE(saved.value(u"values"_s).toMap().value(u"lora"_s).toMap().value(u"region"_s).toString(), u"EU_868"_s);
        QVERIFY(!saved.value(u"values"_s).toMap().contains(u"options"_s));
        QCOMPARE(QFile::permissions(r->profiles->path()) & (QFileDevice::ReadGroup | QFileDevice::ReadOther | QFileDevice::WriteGroup | QFileDevice::WriteOther), QFileDevice::Permissions());
        QVERIFY(r->sw->notes().join(u' ').contains(u"No settings were saved"_s));
    }

    void switching_back_puts_the_saved_settings_back_through_the_engine()
    {
        auto r = rig(radio(u"a1b2c3d4e5f6"_s, u"meshcore"_s, u"v1.9.1"_s), mcConfig());
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->backend->radios().size() == 1, 5000);
        // an earlier visit to Meshtastic left these settings behind
        QVERIFY(r->profiles->save(kDevice, u"meshtastic"_s, mtConfig(u"US"_s, u"MEDIUM_FAST"_s, u"crew"_s).toVariantMap(), u"Heltec V3"_s, u"2.7.15"_s));
        r->eng->onConnect = [e = r->eng.get()](const QString &) {
            e->radiosJson = QJsonArray{radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s)};
            e->cfgByRadio.insert(u"!a1b2c3d4"_s, mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));   // the fresh firmware's own values
        };
        r->sw->start(req(u"meshcore"_s, u"meshtastic"_s));
        QTRY_VERIFY_WITH_TIMEOUT(!r->sw->running(), 30000);
        QVERIFY2(r->sw->step() == RadioSwitch::Step::Done, qPrintable(r->sw->error()));
        QCOMPARE(r->sw->restored(), 3);
        const QJsonObject apply = r->eng->last(u"device.apply"_s).value(u"params"_s).toObject();
        QCOMPARE(apply.value(u"radio"_s).toString(), u"!a1b2c3d4"_s);
        const QJsonObject ch = apply.value(u"changes"_s).toObject();
        QCOMPARE(ch.value(u"lora.region"_s).toString(), u"US"_s);
        QCOMPARE(ch.value(u"lora.modem_preset"_s).toString(), u"MEDIUM_FAST"_s);
        QCOMPARE(ch.value(u"channels.1.name"_s).toString(), u"crew"_s);
        QVERIFY(!ch.contains(u"owner.long_name"_s));   // already the same: not rewritten
        // and the MeshCore side was kept for the next switch
        QVERIFY(r->profiles->has(kDevice, u"meshcore"_s));
    }

    void a_refused_flash_stops_the_sequence_and_says_how_to_get_the_board_back()
    {
        auto r = rig(radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s), mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->backend->radios().size() == 1, 5000);
        r->sw->start(req(u"meshtastic"_s, u"meshcore"_s, u"Wrong name"_s));   // the manager checks the typed name itself
        QTRY_VERIFY_WITH_TIMEOUT(!r->sw->running(), 30000);
        QCOMPARE(r->sw->step(), RadioSwitch::Step::Failed);
        QCOMPARE(r->sw->failedAt(), RadioSwitch::Step::Flashing);
        QVERIFY(!r->sw->error().isEmpty());
        QVERIFY(r->sw->recovery().contains(u"boot button"_s));
        QCOMPARE(r->eng->count(u"radios.connect"_s), 0);   // no reconnect after a failure
        QVERIFY(r->profiles->has(kDevice, u"meshtastic"_s));   // the old settings are still safe
    }

    void a_port_the_engine_cannot_release_means_nothing_is_flashed()
    {
        auto r = rig(radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s), mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->backend->radios().size() == 1, 5000);
        SwitchRequest q = req(u"meshtastic"_s, u"meshcore"_s);
        q.port = u"/dev/ttyNOPE"_s;
        // the fake engine always accepts a release, so ask a backend that refuses
        FakeMeshBackend none;
        RadioSwitch sw(&none, r->fc.get(), r->profiles.get());
        sw.setTimeouts(500, 1000);
        sw.start(q);
        QTRY_VERIFY_WITH_TIMEOUT(!sw.running(), 10000);
        QCOMPARE(sw.step(), RadioSwitch::Step::Failed);
        QCOMPARE(sw.failedAt(), RadioSwitch::Step::Releasing);
        QCOMPARE(r->fw->count(u"POST"_s, u"/v1/flash/run"_s), 0);
        QCOMPARE(r->fw->mints.size(), 0);
    }

    void missing_pieces_are_refused_before_anything_happens()
    {
        auto r = rig(radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s), mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));
        QVERIFY(r);
        SwitchRequest q = req(u"meshtastic"_s, u"meshcore"_s, QString());
        r->sw->start(q);
        QCOMPARE(r->sw->step(), RadioSwitch::Step::Failed);
        QCOMPARE(r->eng->count(u"radios.release"_s), 0);
        QCOMPARE(r->fw->requests.size(), 0);
    }
};

QTEST_MAIN(TstSurvivalSwitch)
#include "tst_survival_switch.moc"
