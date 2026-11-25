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
#include "takengine.h"

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
        std::unique_ptr<TakEngine> tak;
        std::unique_ptr<FirmwareEngine> fe;
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
        r->tak = std::make_unique<TakEngine>(r->backend.get(), r->dir.path() + u"/data"_s);
        r->fe = std::make_unique<FirmwareEngine>(r->fc.get(), u"Test Owner"_s);
        r->tak->setFirmware(r->fe.get());
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
        auto seqOf = [&](const QString &m) { const int i = firstIndex(reqs, m); return i < 0 ? -1 : reqs[i].value(u"_seq"_s).toInt(); };
        const int cfg = seqOf(u"device.config"_s), rel = seqOf(u"radios.release"_s), con = seqOf(u"radios.connect"_s);
        int run = -1;   // the flash goes to the OTHER engine: the order is proved with the counter both stand-ins share
        for (const auto &q : r->fw->requests)
            if (q.path == u"/v1/flash/run"_s)
                run = q.seq;
        QVERIFY(cfg > 0 && rel > cfg && run > rel && con > run);
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
        QVERIFY(r->sw->notes().join(u' ').contains(u"Settings for Meshtastic saved"_s));
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

    void a_fresh_radio_without_the_old_channels_still_gets_the_rest_back_and_the_switch_is_a_success()
    {
        auto r = rig(radio(u"a1b2c3d4e5f6"_s, u"meshcore"_s, u"v1.9.1"_s), mcConfig());
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->backend->radios().size() == 1, 5000);
        // saved earlier: channel 1 "crew" and region US; the fresh Meshtastic firmware only has channel 0
        QJsonObject saved = mtConfig(u"US"_s, u"MEDIUM_FAST"_s, u"crew"_s);
        QVERIFY(r->profiles->save(kDevice, u"meshtastic"_s, saved.toVariantMap(), u"Heltec V3"_s, u"2.7.15"_s));
        r->eng->onConnect = [e = r->eng.get()](const QString &) {
            QJsonObject fresh = mtConfig(u"EU_868"_s, u"LONG_FAST"_s, QString());
            fresh.insert(u"channels"_s, QJsonArray{QJsonObject{{u"index"_s, 0}, {u"name"_s, QString()}, {u"role"_s, u"PRIMARY"_s}, {u"psk_b64"_s, u"AQ=="_s}}});
            e->radiosJson = QJsonArray{radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s)};
            e->cfgByRadio.insert(u"!a1b2c3d4"_s, fresh);
        };
        r->sw->start(req(u"meshcore"_s, u"meshtastic"_s));
        QTRY_VERIFY_WITH_TIMEOUT(!r->sw->running(), 30000);
        QVERIFY2(r->sw->step() == RadioSwitch::Step::Done, qPrintable(r->sw->error()));
        const QJsonObject ch = r->eng->last(u"device.apply"_s).value(u"params"_s).toObject().value(u"changes"_s).toObject();
        QCOMPARE(ch.value(u"lora.region"_s).toString(), u"US"_s);               // the region is put back
        QVERIFY(!ch.contains(u"channels.1.name"_s));                              // the channel the radio lacks is left out, not an error
        QVERIFY(r->sw->notes().join(u' ').contains(u"do not exist on this radio yet"_s));
    }

    void a_receive_only_engine_means_the_earlier_settings_are_kept_but_the_switch_still_succeeds()
    {
        auto r = rig(radio(u"a1b2c3d4e5f6"_s, u"meshcore"_s, u"v1.9.1"_s), mcConfig());
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->backend->radios().size() == 1, 5000);
        QVERIFY(r->profiles->save(kDevice, u"meshtastic"_s, mtConfig(u"US"_s, u"MEDIUM_FAST"_s, u"crew"_s).toVariantMap(), u"Heltec V3"_s, u"2.7.15"_s));
        r->eng->onConnect = [e = r->eng.get()](const QString &) {
            e->radiosJson = QJsonArray{radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s)};
            e->cfgByRadio.insert(u"!a1b2c3d4"_s, mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));
            e->receiveOnly = true;   // the health read after the connect says writes are off
        };
        r->sw->start(req(u"meshcore"_s, u"meshtastic"_s));
        QTRY_VERIFY_WITH_TIMEOUT(!r->sw->running(), 30000);
        QVERIFY2(r->sw->step() == RadioSwitch::Step::Done, qPrintable(r->sw->error()));
        QCOMPARE(r->eng->count(u"device.apply"_s), 0);
        QVERIFY(r->sw->notes().join(u' ').contains(u"receive-only"_s));
        QVERIFY(r->profiles->has(kDevice, u"meshtastic"_s));                      // nothing was thrown away
    }

    void a_radio_that_takes_a_while_to_answer_is_connected_once_not_every_few_seconds()
    {
        auto r = rig(radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s), mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->backend->radios().size() == 1, 5000);
        r->eng->onConnect = [e = r->eng.get()](const QString &) {
            QJsonObject slow = radio(u"a1b2c3d4e5f6"_s, u"meshcore"_s, u"v1.9.1"_s);
            slow.insert(u"state"_s, u"connecting"_s);
            e->radiosJson = QJsonArray{slow};
            e->cfgByRadio.insert(u"a1b2c3d4e5f6"_s, mcConfig());
            QTimer::singleShot(6000, e, [e] { e->radiosJson = QJsonArray{radio(u"a1b2c3d4e5f6"_s, u"meshcore"_s, u"v1.9.1"_s)}; });
        };
        r->sw->start(req(u"meshtastic"_s, u"meshcore"_s));
        QTRY_VERIFY_WITH_TIMEOUT(!r->sw->running(), 40000);
        QVERIFY2(r->sw->step() == RadioSwitch::Step::Done, qPrintable(r->sw->error()));
        QCOMPARE(r->eng->count(u"radios.connect"_s), 1);   // 6 seconds of "connecting" did not make it ask again
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

    void one_row_per_radio_joins_the_engine_the_port_list_and_the_firmware_manager()
    {
        auto r = rig(radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s), mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));
        QVERIFY(r);
        // a second board is plugged in but not open: the engine lists its port, the manager does not know it
        r->eng->serialDevices = QJsonArray{QJsonObject{{u"path"_s, kPort}, {u"vid"_s, 0x10c4}, {u"pid"_s, 0xea60}, {u"product"_s, u"Heltec V3"_s}},
                                           QJsonObject{{u"path"_s, u"/dev/ttyACM0"_s}, {u"vid"_s, 0x303a}, {u"pid"_s, 0x1001}, {u"product"_s, u"ESP32-S3"_s}}};
        r->fe->reload();
        r->fe->detect();
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->fe->state() == u"ready"_s && !r->fe->devices().isEmpty() && !r->fe->library().isEmpty(), 5000);
        r->backend->discoverDevices();
        QTRY_VERIFY_WITH_TIMEOUT(r->tak->radioRows().size() == 2, 5000);
        const QVariantMap open = r->tak->radioRow(kPort);
        QCOMPARE(open.value(u"state"_s).toString(), u"connected"_s);
        QCOMPARE(open.value(u"hardware"_s).toString(), u"Heltec V3"_s);
        QCOMPARE(open.value(u"firmware"_s).toString(), u"2.7.15"_s);          // read from the radio, not guessed
        QCOMPARE(open.value(u"networkName"_s).toString(), u"Meshtastic"_s);
        QCOMPARE(open.value(u"fwDeviceId"_s).toString(), kDevice);            // joined on the port
        QVERIFY(open.value(u"canSwitch"_s).toBool());
        QVERIFY(open.value(u"canSettings"_s).toBool());
        const QVariantMap bare = r->tak->radioRow(u"/dev/ttyACM0"_s);
        QCOMPARE(bare.value(u"state"_s).toString(), u"detected"_s);
        QVERIFY(bare.value(u"canConnect"_s).toBool());
        QVERIFY(!bare.value(u"canSwitch"_s).toBool());                          // the manager does not see it: no switch, and the row says why
        QVERIFY(bare.value(u"firmware"_s).toString().isEmpty());                // unknown stays unknown
        QCOMPARE(r->eng->count(u"radios.connect"_s), 0);                       // listing never opened anything
    }

    void two_radios_one_per_network_are_merged_filtered_and_routed()
    {
        auto r = rig(radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s), mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));
        QVERIFY(r);
        QJsonObject mc = radio(u"a1b2c3d4e5f6"_s, u"meshcore"_s, u"v1.9.1"_s);
        mc.insert(u"port"_s, u"/dev/ttyACM0"_s);
        r->eng->radiosJson.append(mc);
        r->eng->identitiesJson = QJsonArray{QJsonObject{{u"id"_s, u"!28af67cc"_s}, {u"callsign"_s, u"Sam"_s}, {u"owner"_s, QString()}, {u"isSelf"_s, false}, {u"hasPosition"_s, false}, {u"lon"_s, 0}, {u"lat"_s, 0}, {u"lastHeardMs"_s, 1795581844099.0}},
                                            QJsonObject{{u"id"_s, u"7ed23c00aabb"_s}, {u"callsign"_s, u"Ria"_s}, {u"owner"_s, QString()}, {u"isSelf"_s, false}, {u"hasPosition"_s, false}, {u"lon"_s, 0}, {u"lat"_s, 0}, {u"lastHeardMs"_s, 1795581844099.0}}};
        r->eng->receiveOnly = false;
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->backend->radios().size() == 2 && r->backend->identities().size() == 2, 5000);
        QCOMPARE(r->tak->networks().size(), 2);
        const QVariantList all = r->tak->people();
        QCOMPARE(all.size(), 2);
        QSet<QString> nets;
        for (const QVariant &p : all)
            nets << p.toMap().value(u"network"_s).toString();
        QCOMPARE(nets, QSet<QString>({u"meshtastic"_s, u"meshcore"_s}));        // each person carries their network
        r->tak->setNetworkFilter(u"meshcore"_s);
        QCOMPARE(r->tak->people().size(), 1);
        QCOMPARE(r->tak->people().first().toMap().value(u"name"_s).toString(), u"Ria"_s);
        r->tak->setNetworkFilter(QString());
        QCOMPARE(r->tak->people().size(), 2);
        // a send goes to the network the thread belongs to; the engine picks that network's radio
        r->tak->setSendingOn(true);
        QCOMPARE(r->tak->sendBlockedFor(u"ch:meshcore:0"_s), QString());
        QVERIFY(r->tak->sendVia(u"ch:meshcore:0"_s).contains(u"MeshCore"_s));
        QVERIFY(r->tak->sendVia(u"!28af67cc"_s).contains(u"Meshtastic"_s));
        QVERIFY(r->tak->send(u"ch:meshcore:0"_s, u"hello"_s).isEmpty());
        QTRY_VERIFY_WITH_TIMEOUT(r->eng->count(u"app.send"_s) == 1, 3000);
        QCOMPARE(r->eng->last(u"app.send"_s).value(u"params"_s).toObject().value(u"thread"_s).toString(), u"ch:meshcore:0"_s);
        // with no MeshCore radio the message is refused here, in words, and never reaches the engine
        r->eng->radiosJson = QJsonArray{radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s)};
        r->backend->refresh();
        QTRY_COMPARE_WITH_TIMEOUT(r->backend->radios().size(), 1, 5000);
        const QString e = r->tak->send(u"ch:meshcore:0"_s, u"hello"_s);
        QVERIFY2(e.contains(u"MeshCore"_s), qPrintable(e));
        QTest::qWait(300);
        QCOMPARE(r->eng->count(u"app.send"_s), 1);
    }

    void a_channel_the_radio_has_is_a_conversation_before_anyone_has_spoken_on_it()
    {
        auto r = rig(radio(u"!a1b2c3d4"_s, u"meshtastic"_s, u"2.7.15"_s), mtConfig(u"EU_868"_s, u"LONG_FAST"_s, u"family"_s));
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && r->backend->radios().size() == 1, 5000);
        auto find = [&](const QString &id) { for (const QVariant &v : r->tak->threads()) if (v.toMap().value(u"id"_s).toString() == id) return v.toMap(); return QVariantMap(); };
        QVERIFY(!find(u"ch:meshtastic:0"_s).isEmpty());       // the one that was heard
        QVERIFY(find(u"ch:meshtastic:1"_s).isEmpty());        // settings not read yet: no stub
        r->tak->requestConfig(u"!a1b2c3d4"_s);
        QTRY_VERIFY_WITH_TIMEOUT(!find(u"ch:meshtastic:1"_s).isEmpty(), 5000);
        const QVariantMap t = find(u"ch:meshtastic:1"_s);
        QVERIFY(t.value(u"title"_s).toString().contains(u"family"_s));   // named by the radio's own channel name
        QCOMPARE(t.value(u"count"_s).toInt(), 0);
        QCOMPARE(find(u"ch:meshtastic:0"_s).value(u"count"_s).toInt(), 1);
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
