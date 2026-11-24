// SPDX-License-Identifier: AGPL-3.0-or-later
// The D-Bus interface against a PRIVATE bus: the test starts its own dbus-daemon on a socket inside
// its temp directory and refuses to connect anywhere else. It never reaches a real session bus.
#include <QDBusConnection>
#include <QDBusInterface>
#include <QDBusMessage>
#include <QDBusReply>
#include <QJsonArray>
#include <QJsonObject>
#include <QProcess>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QtTest>

#include "service.h"

using namespace Taos::Notifyd;
using namespace Qt::StringLiterals;

class TestService : public QObject
{
    Q_OBJECT
    QTemporaryDir m_dir;
    QProcess m_daemon;
    QString m_addr;
    int m_conn = 0;

    QDBusConnection connect()
    {
        // The guard: only ever the socket this test made.
        Q_ASSERT(m_addr.startsWith(u"unix:path="_s + m_dir.path()));
        return QDBusConnection::connectToBus(m_addr, u"t%1"_s.arg(++m_conn));
    }
    static QDBusMessage call(QDBusConnection &c, const QString &method, const QVariantList &args = {})
    {
        QDBusMessage m = QDBusMessage::createMethodCall(u"org.freedesktop.Notifications"_s, u"/org/freedesktop/Notifications"_s,
                                                       u"org.freedesktop.Notifications"_s, method);
        m.setArguments(args);
        return c.call(m, QDBus::BlockWithGui, 5000);
    }
    static QVariantList notifyArgs(const QString &app, uint replaces, const QString &summary, const QString &body = {},
                                   const QVariantMap &hints = {}, const QStringList &actions = {})
    {
        return {app, replaces, QString(), summary, body, actions, hints, -1};
    }
    struct Rig
    {
        QTemporaryDir dir;
        WriteAheadBuffer buf;
        Forwarder fwd;
        Service svc;
        static WriteAheadBuffer::Options bo(const QString &d, int hard)
        {
            WriteAheadBuffer::Options o; o.dir = d + u"/buf"_s; o.hardMax = hard; return o;
        }
        Rig(const DropList &drop = {}, int hard = 5000)
            : buf(bo(dir.path(), hard)), fwd(&buf, opts(dir.path())), svc(&buf, &fwd, drop)
        {
            buf.open(nullptr);
        }
        static Forwarder::Options opts(const QString &d) { Forwarder::Options f; f.runtimeDir = d + u"/none"_s; return f; }
    };

private Q_SLOTS:
    void initTestCase()
    {
        const QString sock = m_dir.path() + u"/bus.sock"_s;
        m_addr = u"unix:path="_s + sock;
        m_daemon.setProgram(u"dbus-daemon"_s);
        m_daemon.setArguments({u"--session"_s, u"--nofork"_s, u"--address="_s + m_addr});
        m_daemon.start();
        QVERIFY2(m_daemon.waitForStarted(5000), "dbus-daemon not found");
        QTRY_VERIFY_WITH_TIMEOUT(QFile::exists(sock), 5000);
    }
    void cleanup()
    {
        for (int i = 1; i <= m_conn; ++i)
            QDBusConnection::disconnectFromBus(u"t%1"_s.arg(i));
    }
    void cleanupTestCase()
    {
        m_daemon.terminate();
        m_daemon.waitForFinished(3000);
    }

    void required_methods_and_server_information()
    {
        Rig r;
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        const QDBusMessage caps = call(cli, u"GetCapabilities"_s);
        QCOMPARE(caps.type(), QDBusMessage::ReplyMessage);
        QVERIFY(caps.arguments().first().toStringList().contains(u"body"_s));
        const QDBusMessage info = call(cli, u"GetServerInformation"_s);
        QCOMPARE(info.type(), QDBusMessage::ReplyMessage);
        QCOMPARE(info.arguments().size(), 4); // name, vendor, version, spec version
        QCOMPARE(info.arguments()[0].toString(), u"taos-notifyd"_s);
        QCOMPARE(info.arguments()[3].toString(), u"1.2"_s);
        // introspection lists Notify, CloseNotification and both signals
        QDBusMessage m = QDBusMessage::createMethodCall(u"org.freedesktop.Notifications"_s, u"/org/freedesktop/Notifications"_s,
                                                       u"org.freedesktop.DBus.Introspectable"_s, u"Introspect"_s);
        const QString xml = cli.call(m, QDBus::BlockWithGui, 5000).arguments().first().toString();
        for (const char *want : {"\"Notify\"", "\"CloseNotification\"", "\"GetCapabilities\"", "\"GetServerInformation\"",
                                 "\"NotificationClosed\"", "\"ActionInvoked\""})
            QVERIFY2(xml.contains(QString::fromLatin1(want)), want);
    }
    void notify_is_on_disk_before_it_returns()
    {
        Rig r;
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        const QDBusMessage a = call(cli, u"Notify"_s,
                                    notifyArgs(u"Mail"_s, 0, u"New <b>mail</b>"_s, u"A &amp; B <a href=\"x\">link</a>"_s,
                                               {{u"desktop-entry"_s, u"org.example.Mail"_s}, {u"urgency"_s, QVariant::fromValue(uchar(2))}}));
        QCOMPARE(a.type(), QDBusMessage::ReplyMessage);
        const uint id = a.arguments().first().toUInt();
        QVERIFY(id > 0);
        // by the time the reply is here the record is durable (no wait, no kick needed)
        const auto p = r.buf.pending();
        QCOMPARE(p.size(), 1);
        const QJsonObject o = p[0].record;
        QCOMPARE(o[u"summary"_s].toString(), u"New mail"_s);        // markup is removed from the summary too
        QCOMPARE(o[u"body"_s].toString(), u"A & B link"_s);         // body markup stripped
        QCOMPARE(o[u"desktop_entry"_s].toString(), u"org.example.Mail"_s);
        QCOMPARE(o[u"urgency"_s].toString(), u"critical"_s);
        QVERIFY(p[0].critical);
        QVERIFY(o[u"id"_s].toString().startsWith(u"n%1."_s.arg(r.buf.installId())));
    }
    void markup_is_stripped_from_every_text_field()
    {
        Rig r;
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        const QString img = u"<img src=\"http://127.0.0.1:9/x.png\">"_s;
        call(cli, u"Notify"_s, QVariantList{u"App"_s + img, 0u, QString(), img + u"title<b>!</b>"_s, u"b"_s + img,
                                            QStringList{u"k"_s, u"go"_s + img}, QVariantMap{{u"desktop-entry"_s, u"x"_s + img}}, -1});
        const QJsonObject o = r.buf.pending().first().record;
        QCOMPARE(o[u"app_name"_s].toString(), u"App"_s);
        QCOMPARE(o[u"summary"_s].toString(), u"title!"_s);
        QCOMPARE(o[u"body"_s].toString(), u"b"_s);
        QCOMPARE(o[u"desktop_entry"_s].toString(), u"x"_s);
        QCOMPARE(o[u"actions"_s].toArray().first().toObject()[u"label"_s].toString(), u"go"_s);
        QVERIFY(!QJsonDocument(o).toJson().contains("<img"));
    }
    void a_replace_only_applies_to_the_same_sender()
    {
        Rig r;
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection owner = connect(), other = connect();
        const uint id = call(owner, u"Notify"_s, notifyArgs(u"a"_s, 0, u"mine"_s)).arguments().first().toUInt();
        // another app guessing the id gets a NEW notification, never a replacement of the first
        const uint theirs = call(other, u"Notify"_s, notifyArgs(u"b"_s, id, u"theirs"_s)).arguments().first().toUInt();
        QVERIFY(theirs != id);
        QVERIFY(!r.buf.pending().last().record.contains(u"replaces"_s));
        // the owner's own replace still works
        QCOMPARE(call(owner, u"Notify"_s, notifyArgs(u"a"_s, id, u"mine again"_s)).arguments().first().toUInt(), id);
        QVERIFY(r.buf.pending().last().record.contains(u"replaces"_s));
        // an id this run never handed out is a new notification
        const uint ghost = call(owner, u"Notify"_s, notifyArgs(u"a"_s, 99999, u"ghost"_s)).arguments().first().toUInt();
        QVERIFY(ghost != 99999);
    }
    void transient_ones_are_counted_and_critical_ones_are_kept()
    {
        Rig r;
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        call(cli, u"Notify"_s, notifyArgs(u"vol"_s, 0, u"osd"_s, {}, {{u"transient"_s, true}}));
        QCOMPARE(r.buf.count(), 0);
        QCOMPARE(r.svc.transientSkipped(), 1);
        call(cli, u"Notify"_s, notifyArgs(u"bat"_s, 0, u"battery critical"_s, {},
                                          {{u"transient"_s, true}, {u"urgency"_s, QVariant::fromValue(uchar(2))}}));
        QCOMPARE(r.buf.count(), 1);
        QVERIFY(r.buf.pending().first().critical);
        QFile f(r.buf.dir() + u"/status"_s);
        QVERIFY(f.open(QIODevice::ReadOnly));
        const QJsonObject st = QJsonDocument::fromJson(f.readAll()).object();
        QCOMPARE(st[u"transient_skipped"_s].toInt(), 1);
        QVERIFY(!QJsonDocument(st).toJson().contains("osd")); // counts, never content
    }
    void a_failed_counter_write_fails_the_call()
    {
        Rig r;
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        QVERIFY(QDir().mkpath(r.buf.dir() + u"/seq"_s)); // the counter cannot be saved
        QCOMPARE(call(cli, u"Notify"_s, notifyArgs(u"a"_s, 0, u"x"_s)).type(), QDBusMessage::ErrorMessage);
        QCOMPARE(r.buf.count(), 0);
    }
    void replace_returns_the_same_id_and_links_the_rows()
    {
        Rig r;
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        const uint id = call(cli, u"Notify"_s, notifyArgs(u"dl"_s, 0, u"10%"_s)).arguments().first().toUInt();
        const uint again = call(cli, u"Notify"_s, notifyArgs(u"dl"_s, id, u"90%"_s)).arguments().first().toUInt();
        QCOMPARE(again, id);
        const auto p = r.buf.pending();
        QCOMPARE(p.size(), 2);
        QCOMPARE(p[1].record[u"replaces"_s].toString(), p[0].record[u"id"_s].toString()); // points at the first post
        QVERIFY(p[1].record[u"id"_s] != p[0].record[u"id"_s]); // every post has its own id
    }
    void limits_are_applied_before_buffering()
    {
        Rig r;
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        QStringList acts;
        for (int i = 0; i < 12; ++i) acts << QString::number(i) << u"label"_s;
        call(cli, u"Notify"_s, notifyArgs(QString(300, u'a'), 0, QString(1000, u's'), QString(5000, u'b'), {}, acts));
        const QJsonObject o = r.buf.pending().first().record;
        QCOMPARE(o[u"app_name"_s].toString().size(), 128);
        QCOMPARE(o[u"summary"_s].toString().size(), 256);
        QCOMPARE(o[u"body"_s].toString().size(), 2048);
        QCOMPARE(o[u"actions"_s].toArray().size(), 8);
    }
    void close_notification_signals_closed()
    {
        Rig r;
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        QSignalSpy spy(&r.svc, &Service::notificationClosed);
        QCOMPARE(call(cli, u"CloseNotification"_s, {42u}).type(), QDBusMessage::ReplyMessage);
        QCOMPARE(spy.size(), 1);
        QCOMPARE(spy[0][0].toUInt(), 42u);
        QCOMPARE(spy[0][1].toUInt(), 3u); // closed by CloseNotification
    }
    void drop_list_and_transient()
    {
        Rig r(DropList(u"kiosk-browser.desktop, Other"_s));
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        const QDBusMessage a = call(cli, u"Notify"_s,
                                    notifyArgs(u"Chromium"_s, 0, u"web-note"_s, {}, {{u"desktop-entry"_s, u"kiosk-browser"_s}}));
        QCOMPARE(a.type(), QDBusMessage::ReplyMessage); // the app is told it worked
        QCOMPARE(r.buf.count(), 0);
        QCOMPARE(r.svc.filtered(), 1);
        call(cli, u"Notify"_s, notifyArgs(u"other"_s, 0, u"x"_s));                                 // matched on app name too
        QCOMPARE(r.buf.count(), 0);
        call(cli, u"Notify"_s, notifyArgs(u"term"_s, 0, u"from-os-app"_s));                        // control: not on the list
        QCOMPARE(r.buf.count(), 1);
        call(cli, u"Notify"_s, notifyArgs(u"vol"_s, 0, u"osd"_s, {}, {{u"transient"_s, true}}));   // transient: not kept
        QCOMPARE(r.buf.count(), 1);
    }
    void a_buffer_that_cannot_keep_it_fails_the_call()
    {
        Rig r({}, 1);
        QDBusConnection srv = connect();
        QVERIFY(r.svc.start(srv, nullptr));
        QDBusConnection cli = connect();
        QCOMPARE(call(cli, u"Notify"_s, notifyArgs(u"a"_s, 0, u"one"_s)).type(), QDBusMessage::ReplyMessage);
        const QDBusMessage e = call(cli, u"Notify"_s, notifyArgs(u"a"_s, 0, u"two"_s));
        QCOMPARE(e.type(), QDBusMessage::ErrorMessage); // the app sees it; nothing is accepted and lost
    }
    void the_name_is_never_taken_from_another_server()
    {
        Rig first, second;
        QDBusConnection a = connect();
        QVERIFY(first.svc.start(a, nullptr));
        QDBusConnection b = connect();
        QString why;
        QVERIFY(!second.svc.start(b, &why));
        QVERIFY2(why.contains(u"not taking it over"_s), qPrintable(why));
        second.svc.writeStatus(u"idle"_s, why); // what main() does before it exits 0
        QFile sf(second.buf.dir() + u"/status"_s);
        QVERIFY(sf.open(QIODevice::ReadOnly));
        const QJsonObject st = QJsonDocument::fromJson(sf.readAll()).object();
        QCOMPARE(st[u"state"_s].toString(), u"idle"_s);
        QVERIFY(st[u"reason"_s].toString().contains(u"owned by another"_s));
        QDBusConnection cli = connect();
        call(cli, u"Notify"_s, notifyArgs(u"x"_s, 0, u"to the first"_s));
        QCOMPARE(first.buf.count(), 1);
        QCOMPARE(second.buf.count(), 0);
    }
};

QTEST_MAIN(TestService)
#include "test_service.moc"
