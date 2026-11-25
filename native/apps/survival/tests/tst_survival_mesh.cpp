// SPDX-License-Identifier: AGPL-3.0-or-later
// taOStak's engines, driven through the real socket and HTTP paths against stand-in engines that answer with the real
// engines' captured JSON: the socket backend (reads, reconnects, honest absence, never blocks), identity assignment and
// linking, the compose switches (a send reaches the engine only when it is not receive-only AND the owner turned sending
// on), device settings (read only; the write path refuses), the firmware client and its guarded flash, the QR encoder
// against an independent encoder (the python qrcode package), positions-ago wording, and the app's own saved settings. Nothing here opens a radio,
// writes a setting or flashes a board, and the fake engines record every request to prove it.
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "fakemesh.h"
#include "firmwareclient.h"
#include "meshclient.h"
#include "meshformat.h"
#include "meshprefs.h"
#include "qrcode.h"
#include "socketmeshbackend.h"
#include "statestore.h"
#include "takengine.h"

using namespace Taos::Survival;
using namespace FakeEngines;
using namespace Qt::StringLiterals;

#define QVERIFY2_RET(cond, ret) do { if (!(cond)) { QTest::qFail(#cond, __FILE__, __LINE__); return ret; } } while (false)

class TstSurvivalMesh : public QObject
{
    Q_OBJECT
    QString fx() const { return QStringLiteral(MESH_FIXTURES_DIR) + u"/mesh"_s; }
    // the contact the fixtures were captured with (its id is random per capture)
    QString contactId() const { return fixture(fx(), u"identities.list"_s).toObject().value(u"contacts"_s).toArray().first().toObject().value(u"id"_s).toString(); }
    QString fwFx() const { return QStringLiteral(MESH_FIXTURES_DIR) + u"/firmware"_s; }

    struct Rig
    {
        QTemporaryDir dir;
        std::unique_ptr<FakeMeshEngine> eng;
        std::unique_ptr<MeshClient> client;
        std::unique_ptr<SocketMeshBackend> backend;
        std::unique_ptr<TakEngine> tak;
        QString sock() const { return dir.path() + u"/mesh.sock"_s; }
    };
    std::unique_ptr<Rig> rig(bool listen = true, bool receiveOnly = true, bool config = false, int delay = 0)
    {
        auto r = std::make_unique<Rig>();
        r->eng = std::make_unique<FakeMeshEngine>(fx());
        r->eng->receiveOnly = receiveOnly;
        r->eng->configReadable = config;
        r->eng->delayMs = delay;
        if (listen)
            QVERIFY2_RET(r->eng->listen(r->sock()), nullptr);
        r->client = std::make_unique<MeshClient>(r->sock());
        r->backend = std::make_unique<SocketMeshBackend>(r->client.get());
        r->tak = std::make_unique<TakEngine>(r->backend.get(), r->dir.path() + u"/data"_s);
        r->client->start();
        return r;
    }

private Q_SLOTS:
    // ---- the socket backend reads the real daemon's shapes ------------------------------------------------------
    void backend_reads_radios_people_messages_positions_and_contacts()
    {
        auto r = rig();
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        QCOMPARE(r->backend->state(), u"connected"_s);
        QCOMPARE(r->backend->radios().size(), 1);
        QCOMPARE(r->backend->radios().first().id, u"!28af67cc"_s);
        QCOMPARE(r->backend->radios().first().batteryPercent, -1);   // unknown stays unknown
        QCOMPARE(r->backend->identities().size(), 2);
        const auto msgs = r->backend->messages();
        QCOMPARE(msgs.size(), 1);
        const MeshMessage m = msgs.first();
        QCOMPARE(m.threadId, u"ch:meshtastic:0"_s);
        QCOMPARE(m.text, u"hello mesh"_s);
        QCOMPARE(m.trust, u"claimed"_s);
        QCOMPARE(m.senderName, u"Sam"_s);                          // who is behind it, from the engine's own record
        QCOMPARE(m.senderIdentity, u"contact:"_s + contactId());
        QVERIFY(m.hasSignal && m.rssi < 0);
        QVERIFY(!m.hasPosition);                                     // this frame carried none: unknown, not 0,0
        QCOMPARE(r->backend->contacts().size(), 1);
        QCOMPARE(r->backend->contacts().first().name, u"Sam"_s);
        QVERIFY(!r->backend->contacts().first().told);
        QCOMPARE(r->backend->assignments().size(), 1);
        QCOMPARE(r->backend->places().size(), 1);
        QCOMPARE(r->backend->places().first().node, u"!7ed23c00"_s);
        QCOMPARE(r->backend->trail(u"!7ed23c00"_s).size(), 1);
        QVERIFY(r->backend->receiveOnly());
        // reading never writes
        QVERIFY2(r->eng->forbiddenSeen().isEmpty(), qPrintable(r->eng->forbiddenSeen().join(u","_s)));
        QCOMPARE(r->eng->count(u"subscribe"_s), 1);
    }
    void a_record_without_a_hop_figure_or_position_time_keeps_them_unknown()
    {
        const QJsonObject rec{{u"kind"_s, u"text"_s}, {u"dir"_s, u"in"_s}, {u"broadcast"_s, false}, {u"from"_s, u"!1"_s}, {u"to"_s, u"!2"_s},
                              {u"host_ts_ms"_s, 1000}, {u"text"_s, u"x"_s}, {u"signal"_s, QJsonObject{{u"rssi"_s, -90}}},
                              {u"position"_s, QJsonObject{{u"lat"_s, 1.5}, {u"lon"_s, 2.5}}}};
        const MeshMessage m = SocketMeshBackend::messageFromRecord(rec);
        QCOMPARE(m.hops, -1);
        QCOMPARE(m.positionTimeMs, qint64(0));
        QVERIFY(m.hasSignal);
        QCOMPARE(m.threadId, u"!1"_s);   // a direct message belongs to the peer's thread
        // a position the engine marks last_known keeps that mark and its own time
        const MeshMessage k = SocketMeshBackend::messageFromRecord(QJsonObject{{u"kind"_s, u"text"_s}, {u"dir"_s, u"out"_s}, {u"broadcast"_s, true}, {u"network"_s, u"meshtastic"_s}, {u"channel"_s, 2},
                              {u"position"_s, QJsonObject{{u"lat"_s, 1.0}, {u"lon"_s, 2.0}, {u"time"_s, 1700000000}, {u"source"_s, u"last_known"_s}}}});
        QVERIFY(k.hasPosition && k.positionIsLastKnown);
        QCOMPARE(k.positionTimeMs, qint64(1700000000) * 1000);
        QCOMPARE(k.threadId, u"ch:meshtastic:2"_s);
        QVERIFY(k.outgoing);
    }

    // ---- absence, reconnection, never blocking --------------------------------------------------------------------
    void no_engine_says_so_and_refuses_everything_in_words()
    {
        auto r = rig(false);
        QVERIFY(r);
        QTest::qWait(300);
        QVERIFY(!r->backend->available());
        QCOMPARE(r->backend->state(), u"absent"_s);
        QVERIFY(r->backend->unavailableReason().contains(u"not running"_s));
        QVERIFY(!r->backend->send(u"!1"_s, u"hi"_s).accepted);
        QVERIFY(!r->tak->canSend());
        QVERIFY(!r->tak->sendBlocked().isEmpty());
        QSignalSpy done(r->backend.get(), &MeshBackend::actionDone);
        r->backend->assign(u"!1"_s, u"agent:x"_s, QString());
        QTRY_COMPARE_WITH_TIMEOUT(done.size(), 1, 2000);
        QVERIFY(!done.first().at(1).toBool());
        QVERIFY(!done.first().at(2).toString().isEmpty());
    }
    void it_connects_when_the_engine_appears_and_again_after_it_goes_away()
    {
        auto r = rig(false);
        QVERIFY(r);
        QVERIFY(!r->backend->available());
        QVERIFY(r->eng->listen(r->sock()));
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 8000);
        r->eng->close();
        QTRY_VERIFY_WITH_TIMEOUT(!r->backend->available(), 5000);
        QCOMPARE(r->backend->state(), u"absent"_s);
        QVERIFY(r->eng->listen(r->sock()));
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 20000);
        QCOMPARE(r->backend->radios().size(), 1);
    }
    void a_slow_engine_never_blocks_the_caller()
    {
        auto r = rig(true, true, false, 400);
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->client->state() == MeshClient::State::Connected, 5000);
        QElapsedTimer t;
        t.start();
        r->backend->refresh();
        r->tak->refresh();
        QVERIFY2(t.elapsed() < 100, "refresh waited for the engine");
        // while the answers are pending the window still gets events
        QCoreApplication::processEvents();
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 8000);
    }
    void the_record_stream_triggers_one_coalesced_refresh()
    {
        auto r = rig();
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        QTRY_COMPARE_WITH_TIMEOUT(r->eng->count(u"subscribe"_s), 1, 3000);
        const int before = r->eng->count(u"health"_s);
        for (int i = 0; i < 5; ++i)
            r->eng->pushRecord();
        QTRY_COMPARE_WITH_TIMEOUT(r->eng->count(u"health"_s), before + 1, 3000);
        QTest::qWait(600);
        QCOMPARE(r->eng->count(u"health"_s), before + 1);
    }

    void a_refused_list_request_is_a_failure_not_an_empty_list()
    {
        auto r = rig();
        QVERIFY(r);
        r->eng->refuseMessages = true;
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->state() == u"failed"_s, 5000);
        QVERIFY(!r->backend->available());
        QVERIFY(r->backend->unavailableReason().contains(u"bad filter"_s));
        QVERIFY(!r->tak->canSend());
        // and it recovers when the engine stops refusing
        r->eng->refuseMessages = false;
        r->backend->refresh();
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
    }
    void sending_can_be_turned_off_again_and_the_choice_is_remembered()
    {
        QTemporaryDir d;
        auto r = rig(true, false);
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        r->tak.reset(new TakEngine(r->backend.get(), d.path()));
        r->tak->setSendingOn(true);
        QVERIFY(r->tak->canSend());
        r->tak->setSendingOn(false);
        QVERIFY(!r->tak->canSend());
        TakEngine again(r->backend.get(), d.path());
        QVERIFY(!again.sendingOn());
    }

    // ---- connecting radios from the app (fakes only: no device is ever opened) ----------------------------------------------
    void ports_are_listed_but_nothing_is_opened_until_the_owner_presses_connect()
    {
        auto r = rig();
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        QVERIFY(r->tak->canConnect());
        r->tak->discover();
        QTRY_COMPARE_WITH_TIMEOUT(r->tak->devices().size(), 2, 3000);
        const QVariantMap usb = r->tak->devices().first().toMap();
        QCOMPARE(usb.value(u"id"_s).toString(), u"/dev/ttyACM0"_s);
        QCOMPARE(usb.value(u"kind"_s).toString(), u"usb"_s);
        QVERIFY(usb.value(u"detail"_s).toString().contains(u"303a:1001"_s));
        QVERIFY(usb.value(u"supported"_s).toBool());
        const QVariantMap ble = r->tak->devices().last().toMap();
        QVERIFY(!ble.value(u"supported"_s).toBool());                   // the engine has no Bluetooth link yet: not offered
        QTest::qWait(200);
        QCOMPARE(r->eng->count(u"radios.connect"_s), 0);                 // listing never connects
        QVERIFY(r->eng->forbiddenSeen().isEmpty());
        r->tak->connectDevice(u"/dev/ttyACM0"_s);
        QCOMPARE(r->tak->connecting(), u"/dev/ttyACM0"_s);               // the handshake is shown while it runs
        QTRY_COMPARE_WITH_TIMEOUT(r->eng->count(u"radios.connect"_s), 1, 3000);
        QCOMPARE(r->eng->last(u"radios.connect"_s).value(u"params"_s).toObject().value(u"device"_s).toString(), u"/dev/ttyACM0"_s);
        QTRY_VERIFY_WITH_TIMEOUT(r->tak->connecting().isEmpty(), 3000);
        QTRY_VERIFY_WITH_TIMEOUT(r->tak->devices().first().toMap().value(u"saved"_s).toBool(), 3000);   // it reconnects after a restart
        r->tak->forgetDevice(u"/dev/ttyACM0"_s);
        QTRY_COMPARE_WITH_TIMEOUT(r->eng->count(u"radios.disconnect"_s), 1, 3000);
        QTRY_VERIFY_WITH_TIMEOUT(!r->tak->devices().first().toMap().value(u"saved"_s).toBool(), 3000);
    }
    void a_busy_port_and_a_missing_permission_are_explained_in_words()
    {
        QVERIFY(SocketMeshBackend::explainConnectError(u"/dev/ttyACM0: Permission denied (os error 13)"_s).contains(u"dialout"_s));
        QVERIFY(SocketMeshBackend::explainConnectError(u"/dev/ttyACM0: Device or resource busy (os error 16)"_s).contains(u"Another program"_s));
        QCOMPARE(SocketMeshBackend::explainConnectError(u"not a mesh radio"_s), u"not a mesh radio"_s);
        auto r = rig();
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        r->eng->connectError = u"/dev/ttyACM0: Permission denied (os error 13)"_s;
        r->tak->discover();
        QTRY_VERIFY_WITH_TIMEOUT(!r->tak->devices().isEmpty(), 3000);
        r->tak->connectDevice(u"/dev/ttyACM0"_s);
        QTRY_VERIFY_WITH_TIMEOUT(r->tak->connecting().isEmpty() && !r->tak->devices().first().toMap().value(u"error"_s).toString().isEmpty(), 3000);
        QVERIFY(r->tak->devices().first().toMap().value(u"error"_s).toString().contains(u"dialout"_s));
        // the error stays for the next try; a good try clears it and remembers the port
        r->eng->connectError.clear();
        r->tak->connectDevice(u"/dev/ttyACM0"_s);
        QTRY_VERIFY_WITH_TIMEOUT(r->tak->devices().first().toMap().value(u"error"_s).toString().isEmpty() && r->tak->devices().first().toMap().value(u"saved"_s).toBool(), 3000);
    }
    void an_engine_that_cannot_open_radios_refuses_connect_in_words()
    {
        auto r = rig();
        QVERIFY(r);
        r->eng->radiosConnect = false;
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        QVERIFY(!r->tak->canConnect());
        r->tak->discover();
        QTRY_VERIFY_WITH_TIMEOUT(!r->tak->devices().isEmpty(), 3000);
        r->tak->connectDevice(u"/dev/ttyACM0"_s);
        QTRY_VERIFY_WITH_TIMEOUT(!r->tak->devices().first().toMap().value(u"error"_s).toString().isEmpty(), 3000);
        QCOMPARE(r->eng->count(u"radios.connect"_s), 0);
    }

    // ---- identities: assign, create a contact, told, link ----------------------------------------------------------
    void assigning_a_radio_sends_the_assignment_and_refreshes()
    {
        auto r = rig();
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        QSignalSpy done(r->backend.get(), &MeshBackend::actionDone);
        r->tak->assign(u"!7ed23c00"_s, r->tak->identityOf(u"agent"_s, u"scout"_s), u"Scout"_s);
        QTRY_COMPARE_WITH_TIMEOUT(done.size(), 1, 3000);
        QVERIFY(done.first().at(1).toBool());
        const QJsonObject p = r->eng->last(u"identities.assign"_s).value(u"params"_s).toObject();
        QCOMPARE(p.value(u"node"_s).toString(), u"!7ed23c00"_s);
        QCOMPARE(p.value(u"identity"_s).toString(), u"agent:scout"_s);
        QCOMPARE(p.value(u"name"_s).toString(), u"Scout"_s);
        const int h = r->eng->count(u"identities.list"_s);
        QTRY_VERIFY_WITH_TIMEOUT(r->eng->count(u"identities.list"_s) >= 2, 3000);
        Q_UNUSED(h);
        // unassigning sends null, not an empty string
        r->tak->assign(u"!7ed23c00"_s, QString());
        QTRY_VERIFY_WITH_TIMEOUT(r->eng->count(u"identities.assign"_s) == 2, 3000);
        QVERIFY(r->eng->last(u"identities.assign"_s).value(u"params"_s).toObject().value(u"identity"_s).isNull());
        // an empty id is not an identity
        QCOMPARE(r->tak->identityOf(u"agent"_s, u"  "_s), QString());
    }
    void creating_a_contact_assigns_the_radio_to_the_new_contact()
    {
        auto r = rig();
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        r->tak->createContact(u"Alex Kim"_s, u"met on the trail"_s, u"!99ff0011"_s);
        QTRY_VERIFY_WITH_TIMEOUT(r->eng->count(u"identities.assign"_s) == 1, 3000);
        QCOMPARE(r->eng->last(u"contacts.create"_s).value(u"params"_s).toObject().value(u"name"_s).toString(), u"Alex Kim"_s);
        const QJsonObject a = r->eng->last(u"identities.assign"_s).value(u"params"_s).toObject();
        QCOMPARE(a.value(u"identity"_s).toString(), u"contact:c9"_s);   // the id the engine returned
        QCOMPARE(a.value(u"node"_s).toString(), u"!99ff0011"_s);
        // a contact needs a name
        const int n = r->eng->count(u"contacts.create"_s);
        r->tak->createContact(u"   "_s, QString(), QString());
        QTest::qWait(100);
        QCOMPARE(r->eng->count(u"contacts.create"_s), n);
        QVERIFY(r->tak->noticeIsError());
    }
    void told_and_link_flows()
    {
        auto r = rig();
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        r->tak->confirmTold(contactId());
        QTRY_COMPARE_WITH_TIMEOUT(r->eng->count(u"contacts.told"_s), 1, 3000);
        const QJsonObject t = r->eng->last(u"contacts.told"_s).value(u"params"_s).toObject();
        QCOMPARE(t.value(u"told"_s).toBool(), true);
        QVERIFY(QDate::fromString(t.value(u"date"_s).toString(), Qt::ISODate).isValid());   // the confirm carries today's date
        // linking needs an account; the engine gets person:<id>
        r->tak->link(contactId(), u""_s);
        QTest::qWait(100);
        QCOMPARE(r->eng->count(u"link"_s), 0);
        r->tak->link(contactId(), u"priya"_s);
        QTRY_COMPARE_WITH_TIMEOUT(r->eng->count(u"link"_s), 1, 3000);
        QCOMPARE(r->eng->last(u"link"_s).value(u"params"_s).toObject().value(u"account"_s).toString(), u"person:priya"_s);
        r->tak->link(contactId(), u"person:amy"_s);
        QTRY_COMPARE_WITH_TIMEOUT(r->eng->count(u"link"_s), 2, 3000);
        QCOMPARE(r->eng->last(u"link"_s).value(u"params"_s).toObject().value(u"account"_s).toString(), u"person:amy"_s);
    }
    void people_threads_and_unread_are_built_from_the_records()
    {
        auto r = rig();
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available() && !r->tak->threads().isEmpty(), 5000);
        const QVariantMap sam = r->tak->person(u"!28af67cc"_s);
        QCOMPARE(sam.value(u"name"_s).toString(), u"Sam"_s);
        QCOMPARE(sam.value(u"kind"_s).toString(), u"contact"_s);
        const QVariantMap other = r->tak->person(u"!7ed23c00"_s);
        QCOMPARE(other.value(u"kind"_s).toString(), u"none"_s);   // nobody assigned
        QVERIFY(other.value(u"hasPosition"_s).toBool());
        QCOMPARE(r->tak->places().size(), 1);
        const QVariantList th = r->tak->threads();
        QCOMPARE(th.first().toMap().value(u"id"_s).toString(), u"ch:meshtastic:0"_s);
        QCOMPARE(th.first().toMap().value(u"unread"_s).toInt(), 1);
        QCOMPARE(r->tak->unreadTotal(), 1);
        const QVariantList ms = r->tak->messages(u"ch:meshtastic:0"_s);
        QCOMPARE(ms.size(), 1);
        QCOMPARE(ms.first().toMap().value(u"who"_s).toString(), u"Sam"_s);
        QVERIFY(ms.first().toMap().value(u"signalText"_s).toString().contains(u"dBm"_s));
        QCOMPARE(ms.first().toMap().value(u"positionText"_s).toString(), QString());   // no position, none invented
        r->tak->markRead(u"ch:meshtastic:0"_s);
        QCOMPARE(r->tak->unreadTotal(), 0);
        // unread marks survive a restart of the app
        TakEngine again(r->backend.get(), r->dir.path() + u"/data"_s);
        QCOMPARE(again.unreadTotal(), 0);
    }

    // ---- compose: the disabled states ------------------------------------------------------------------------------
    void sending_needs_the_engine_not_receive_only_and_the_owner_to_turn_it_on()
    {
        // 1. receive-only engine, owner turned sending on: still refused, nothing reaches the engine
        {
            auto r = rig(true, true);
            QVERIFY(r);
            QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
            r->tak->setSendingOn(true);
            QVERIFY(!r->tak->canSend());
            QVERIFY(r->tak->sendBlocked().contains(u"receive-only"_s));
            QVERIFY(!r->tak->send(u"!7ed23c00"_s, u"hello"_s).isEmpty());
            QTest::qWait(150);
            QVERIFY2(r->eng->forbiddenSeen().isEmpty(), "a send reached a receive-only engine");
        }
        // 2. engine allows sending but the owner has not turned it on (the default)
        {
            auto r = rig(true, false);
            QVERIFY(r);
            QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
            QVERIFY(!r->tak->sendingOn());
            QVERIFY(!r->tak->canSend());
            QVERIFY(r->tak->sendBlocked().contains(u"off"_s));
            QVERIFY(!r->tak->send(u"!7ed23c00"_s, u"hello"_s).isEmpty());
            QTest::qWait(150);
            QVERIFY(r->eng->forbiddenSeen().isEmpty());
            // 3. both: it goes out as the engine's own app.send, once
            r->tak->setSendingOn(true);
            QVERIFY(r->tak->canSend());
            QCOMPARE(r->tak->send(u"!7ed23c00"_s, u"  hello  "_s), QString());
            QTRY_COMPARE_WITH_TIMEOUT(r->eng->count(u"app.send"_s), 1, 3000);
            const QJsonObject p = r->eng->last(u"app.send"_s).value(u"params"_s).toObject();
            QCOMPARE(p.value(u"thread"_s).toString(), u"!7ed23c00"_s);
            QCOMPARE(p.value(u"text"_s).toString(), u"hello"_s);
            QCOMPARE(r->eng->forbiddenSeen({u"app.send"_s}).size(), 0);
            // an empty message is refused before anything is sent
            QVERIFY(!r->tak->send(u"!7ed23c00"_s, u"   "_s).isEmpty());
            QCOMPARE(r->eng->count(u"app.send"_s), 1);
        }
        // 4. the switch is remembered, and a damaged settings file leaves sending OFF
        {
            QTemporaryDir d;
            MeshPrefs a(d.path());
            QVERIFY(!a.sendingOn());
            a.setSendingOn(true);
            QVERIFY(a.save());
            MeshPrefs b(d.path());
            QVERIFY(b.load());
            QVERIFY(b.sendingOn());
            QFile f(b.path());
            QVERIFY(f.open(QIODevice::WriteOnly | QIODevice::Truncate));
            f.write("{ not json");
            f.close();
            MeshPrefs c(d.path());
            QVERIFY(!c.load());
            QVERIFY(!c.sendingOn());
        }
    }

    // ---- device settings: read through the engine, the write path stays shut -----------------------------------------
    void settings_are_unsupported_until_the_engine_reads_them_and_apply_refuses()
    {
        auto r = rig(true, true, false);
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        QVERIFY(!r->tak->configReadable());
        r->tak->requestConfig(u"!28af67cc"_s);
        QCOMPARE(r->tak->config(u"!28af67cc"_s).value(u"state"_s).toString(), u"unsupported"_s);
        QVERIFY(r->tak->config(u"!28af67cc"_s).value(u"values"_s).toMap().isEmpty());   // no invented defaults
        QVERIFY(!r->tak->applyConfig(u"!28af67cc"_s, {{u"owner.long_name"_s, u"x"_s}}).isEmpty());
        QCOMPARE(r->eng->count(u"device.config"_s), 0);
        QCOMPARE(r->eng->count(u"device.apply"_s), 0);
    }
    void settings_are_read_when_the_engine_reports_support_and_never_written()
    {
        auto r = rig(true, true, true);
        QVERIFY(r);
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        QVERIFY(r->tak->configReadable());
        QVERIFY(!r->tak->configWritable());
        r->tak->requestConfig(u"!28af67cc"_s);
        QTRY_COMPARE_WITH_TIMEOUT(r->tak->config(u"!28af67cc"_s).value(u"state"_s).toString(), u"ok"_s, 3000);
        const QVariantMap v = r->tak->config(u"!28af67cc"_s).value(u"values"_s).toMap();
        QCOMPARE(v.value(u"owner"_s).toMap().value(u"long_name"_s).toString(), u"Fixture radio"_s);
        QCOMPARE(v.value(u"channels"_s).toList().size(), 1);
        // the QR of the channel's share link is a real code
        QVERIFY(!r->tak->qr(v.value(u"channels"_s).toList().first().toMap().value(u"share_url"_s).toString()).isEmpty());
        // a write attempt is refused in words and does not reach the engine
        const QString e = r->tak->applyConfig(u"!28af67cc"_s, {{u"owner.long_name"_s, u"changed"_s}});
        QVERIFY(!e.isEmpty());
        QTest::qWait(150);
        QCOMPARE(r->eng->count(u"device.apply"_s), 0);
        QVERIFY(r->eng->forbiddenSeen().isEmpty());
    }
    void asking_for_settings_before_the_engine_has_answered_is_asked_again_once_it_has()
    {
        auto r = rig(true, true, true, 80);
        QVERIFY(r);
        r->tak->requestConfig(u"!28af67cc"_s);   // before health has landed
        QCOMPARE(r->tak->config(u"!28af67cc"_s).value(u"state"_s).toString(), u"loading"_s);
        QTRY_COMPARE_WITH_TIMEOUT(r->tak->config(u"!28af67cc"_s).value(u"state"_s).toString(), u"ok"_s, 5000);
    }

    // ---- wording -------------------------------------------------------------------------------------------------
    void ago_wording_covers_now_minutes_hours_days_future_and_unknown()
    {
        const qint64 now = 1'800'000'000'000;
        const qint64 min = 60'000;
        QCOMPARE(formatAgo(now - 20'000, now), u"just now"_s);
        QCOMPARE(formatAgo(now - 1 * min, now), u"1 minute ago"_s);
        QCOMPARE(formatAgo(now - 5 * min, now), u"5 minutes ago"_s);
        QCOMPARE(formatAgo(now - 59 * min, now), u"59 minutes ago"_s);
        QCOMPARE(formatAgo(now - 60 * min, now), u"1 hour ago"_s);
        QCOMPARE(formatAgo(now - 5 * 60 * min, now), u"5 hours ago"_s);
        QCOMPARE(formatAgo(now - 48 * 60 * min, now), u"2 days ago"_s);
        QVERIFY(formatAgo(now + 10 * min, now).contains(u"ahead"_s));    // never "-10 minutes ago"
        QCOMPARE(formatAgo(now + 30'000, now), u"just now"_s);            // a little clock drift is not alarming
        QCOMPARE(formatAgo(0, now), u"time unknown"_s);                    // unknown is not "56 years ago"
    }

    void the_socket_is_only_ever_sought_in_the_runtime_directory()
    {
        const QByteArray keepSock = qgetenv("TAOS_MESH_SOCKET"), keepRt = qgetenv("XDG_RUNTIME_DIR");
        qunsetenv("TAOS_MESH_SOCKET");
        qunsetenv("XDG_RUNTIME_DIR");
        QCOMPARE(MeshClient::defaultSocketPath(), QString());          // never /tmp
        MeshClient none(MeshClient::defaultSocketPath());
        none.start();
        QTest::qWait(150);
        QVERIFY(none.state() == MeshClient::State::Absent);
        qputenv("XDG_RUNTIME_DIR", "/run/user/4242");
        QCOMPARE(MeshClient::defaultSocketPath(), u"/run/user/4242/taos-mesh.sock"_s);
        qputenv("TAOS_MESH_SOCKET", "/explicit/path.sock");
        QCOMPARE(MeshClient::defaultSocketPath(), u"/explicit/path.sock"_s);
        qunsetenv("TAOS_MESH_SOCKET");
        qunsetenv("XDG_RUNTIME_DIR");
        if (!keepSock.isEmpty()) qputenv("TAOS_MESH_SOCKET", keepSock);
        if (!keepRt.isEmpty()) qputenv("XDG_RUNTIME_DIR", keepRt);
    }

    // ---- the QR encoder against an independent one -----------------------------------------------------------------
    void qr_codes_match_a_reference_encoder_module_for_module()
    {
        QFile f(QStringLiteral(MESH_FIXTURES_DIR) + u"/qr_expected.json"_s);
        QVERIFY(f.open(QIODevice::ReadOnly));
        const QJsonArray all = QJsonDocument::fromJson(f.readAll()).array();
        QVERIFY(all.size() >= 12);
        for (const QJsonValue &v : all) {
            const QJsonObject o = v.toObject();
            const QrResult r = qrEncode(o.value(u"text"_s).toString().toUtf8(), o.value(u"ecc"_s).toString() == u"m"_s);
            const QString label = u"%1 ecc %2"_s.arg(o.value(u"text"_s).toString().left(20), o.value(u"ecc"_s).toString());
            QVERIFY2(r.ok, qPrintable(label));
            QCOMPARE(r.version, o.value(u"version"_s).toInt());
            const QStringList rows = qrRows(r);
            // the reference made the same data with each of the eight masks; ours must be exactly one of them (same data
            // bits, same error correction, same format and version areas). Which mask is best is the encoder's choice.
            bool matched = false;
            for (const QJsonValue &mv : o.value(u"masks"_s).toArray()) {
                const QJsonArray exp = mv.toArray();
                bool same = exp.size() == rows.size();
                for (int y = 0; same && y < rows.size(); ++y)
                    same = rows[y] == exp[y].toString();
                matched = matched || same;
            }
            QVERIFY2(matched, qPrintable(label));
        }
        QVERIFY(!qrEncode(QByteArray(5000, 'x')).ok);   // too long says so
    }

    // ---- saved state from before the mesh engine survives -----------------------------------------------------------
    void an_old_state_file_is_untouched_by_mesh_actions()
    {
        QTemporaryDir d;
        const QByteArray old = R"({"version":1,"markers":[{"id":"m1","name":"Spring","lon":-4.2,"lat":55.8}],"contacts":[{"id":"k1","callsign":"ALPHA","nodeId":"!1234abcd","note":""}],"me":{"lon":-4.0,"lat":55.0}})";
        QFile f(d.path() + u"/state.json"_s);
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write(old);
        f.close();
        auto r = rig();
        QVERIFY(r);
        r->tak.reset();
        r->tak = std::make_unique<TakEngine>(r->backend.get(), d.path());
        QTRY_VERIFY_WITH_TIMEOUT(r->backend->available(), 5000);
        r->tak->createContact(u"New"_s, QString(), QString());
        r->tak->markRead(u"ch:meshtastic:0"_s);
        r->tak->setSendingOn(true);
        QTest::qWait(200);
        StateStore s(d.path());
        QVERIFY(s.load());
        QCOMPARE(s.contacts().size(), 1);
        QCOMPARE(s.contacts().first().toObject().value(u"callsign"_s).toString(), u"ALPHA"_s);
        QCOMPARE(s.markers().size(), 1);
        QVERIFY(s.hasMe());
        QFile again(d.path() + u"/state.json"_s);
        QVERIFY(again.open(QIODevice::ReadOnly));
        QCOMPARE(again.readAll(), old);   // not a byte changed
    }

    // ---- the firmware client and its guarded flash -----------------------------------------------------------------------
    void firmware_library_licences_and_boards_load_through_the_token_api()
    {
        QTemporaryDir d;
        FakeFirmware fw(fwFx(), d.path());
        FirmwareClient c;
        c.setDir(d.path());
        c.setPort(fw.port());
        FirmwareEngine e(&c, u"Test Owner"_s);
        e.reload();
        QTRY_COMPARE_WITH_TIMEOUT(e.state(), u"ready"_s, 5000);
        QTRY_VERIFY_WITH_TIMEOUT(!e.boards().isEmpty() && !e.accepted().isEmpty(), 5000);
        QVERIFY(e.library().size() > 10);
        QCOMPARE(e.pending().size(), 0);
        const QVariantMap first = e.library().first().toMap();
        QVERIFY(first.value(u"unsigned"_s).toBool());                  // the engine's "unsigned by publisher" flag is carried
        QVERIFY(first.value(u"sizeBytes"_s).toDouble() > 0);
        for (const auto &r : fw.requests)
            QCOMPARE(r.auth, QByteArray("Bearer test-token"));
        QCOMPARE(fw.count(u"POST"_s, u"/v1/flash/run"_s), 0);
    }
    void firmware_without_a_running_manager_is_offline_in_words()
    {
        QTemporaryDir d;
        FirmwareClient c;
        c.setDir(d.path());   // no token file: the manager never started
        FirmwareEngine e(&c, u"x"_s);
        e.reload();
        QTRY_COMPARE_WITH_TIMEOUT(e.state(), u"offline"_s, 3000);
        QVERIFY(e.error().contains(u"not running"_s));
        QCOMPARE(e.library().size(), 0);
    }
    void a_pending_licence_is_accepted_by_name_once()
    {
        QTemporaryDir d;
        FakeFirmware fw(fwFx(), d.path());
        fw.mode = u"pending"_s;
        FirmwareClient c;
        c.setDir(d.path());
        c.setPort(fw.port());
        FirmwareEngine e(&c, u"Test Owner"_s);
        e.reload();
        QTRY_COMPARE_WITH_TIMEOUT(e.pending().size(), 3, 5000);
        QCOMPARE(e.pending().first().toMap().value(u"licence_id"_s).toString(), u"GPL-3.0-only"_s);
        e.acceptLicence(u"meshtastic"_s);
        QTRY_COMPARE_WITH_TIMEOUT(fw.count(u"POST"_s, u"/v1/licences/accept"_s), 1, 3000);
        const QJsonObject b = fw.last(u"/v1/licences/accept"_s).body;
        QCOMPARE(b.value(u"network"_s).toString(), u"meshtastic"_s);
        QCOMPARE(b.value(u"by"_s).toString(), u"Test Owner"_s);   // a named person, never anonymous
    }
    void flashing_needs_a_plan_and_the_typed_board_name_and_never_runs_otherwise()
    {
        QTemporaryDir d;
        FakeFirmware fw(fwFx(), d.path());
        FirmwareClient c;
        c.setDir(d.path());
        c.setPort(fw.port());
        FirmwareEngine e(&c, u"Test Owner"_s);
        e.reload();
        e.detect();
        QTRY_VERIFY_WITH_TIMEOUT(!e.devices().isEmpty() && !e.library().isEmpty(), 5000);
        const QString dev = e.devices().first().toMap().value(u"id"_s).toString();
        const QString entry = u"meshtastic:heltec-v3:2.7.26.54e0d8d"_s;
        // no plan yet: nothing is sent
        e.runFlash(dev, entry, u"Heltec V3"_s, false);
        QCOMPARE(fw.count(u"POST"_s, u"/v1/flash/run"_s), 0);
        QVERIFY(!e.result().value(u"ok"_s).toBool());
        // the plan only plans
        e.planFlash(dev, entry);
        QTRY_VERIFY_WITH_TIMEOUT(!e.plan().isEmpty(), 3000);
        QCOMPARE(e.plan().value(u"confirm_phrase"_s).toString(), u"Heltec V3"_s);
        QVERIFY(!e.plan().value(u"warnings"_s).toList().isEmpty());     // unsigned-by-publisher warning is carried
        QCOMPARE(fw.count(u"POST"_s, u"/v1/flash/run"_s), 0);
        // an empty confirmation is refused on this side
        e.runFlash(dev, entry, u"  "_s, false);
        QCOMPARE(fw.count(u"POST"_s, u"/v1/flash/run"_s), 0);
        // the typed name goes to the engine, which checks it again; a wrong name is refused there
        e.runFlash(dev, entry, u"heltec"_s, false);
        QTRY_COMPARE_WITH_TIMEOUT(fw.count(u"POST"_s, u"/v1/flash/run"_s), 1, 3000);
        QTRY_VERIFY_WITH_TIMEOUT(!e.busy() && e.result().contains(u"code"_s), 3000);
        QCOMPARE(e.result().value(u"code"_s).toString(), u"confirmation_required"_s);
        // the right name (against the fake board) completes, and the actor is the person, not an agent
        e.runFlash(dev, entry, u"Heltec V3"_s, false);
        QTRY_COMPARE_WITH_TIMEOUT(fw.count(u"POST"_s, u"/v1/flash/run"_s), 2, 3000);
        QTRY_VERIFY_WITH_TIMEOUT(e.result().value(u"ok"_s).toBool(), 3000);
        const QJsonObject body = fw.last(u"/v1/flash/run"_s).body;
        QCOMPARE(body.value(u"confirm"_s).toString(), u"Heltec V3"_s);
        QCOMPARE(body.value(u"actor"_s).toString(), u"person"_s);   // the engine accepts exactly this word
        QVERIFY(!body.contains(u"allow_no_backup"_s));
        QCOMPARE(e.result().value(u"verify"_s).toMap().value(u"status"_s).toString(), u"confirmed"_s);
    }
    void the_firmware_client_only_ever_talks_to_loopback()
    {
        QTemporaryDir d;
        FakeFirmware fw(fwFx(), d.path());
        FirmwareClient c;
        c.setDir(d.path());
        c.setPort(fw.port());
        bool ran = false;
        c.request(u"GET"_s, u"/v1/status"_s, {}, this, [&](const FwReply &r) { ran = true; QVERIFY(r.reached); });
        QTRY_VERIFY_WITH_TIMEOUT(ran, 3000);
        QVERIFY(!fw.requests.isEmpty());
    }
};

QTEST_MAIN(TstSurvivalMesh)
#include "tst_survival_mesh.moc"
