// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QCoreApplication>
#include <QFile>
#include <QLocalServer>
#include <QLocalSocket>
#include <QTemporaryDir>
#include <QtTest>
#include <sys/stat.h>
#include <thread>

#include "control.h"
#include "swayipc.h"

class TestIpc : public QObject {
    Q_OBJECT
private slots:
    // ---- the i3/sway wire format ----
    void wireRoundTrip() {
        const QByteArray b = SwayWire::encode(SwayWire::GetTree, "hello");
        QCOMPARE(b.left(6), QByteArray("i3-ipc"));
        QCOMPARE(b.size(), 14 + 5);
        SwayWire::Parser p;
        p.feed(b);
        SwayWire::Message m;
        QVERIFY(p.next(&m));
        QCOMPARE(m.type, quint32(SwayWire::GetTree));
        QCOMPARE(m.payload, QByteArray("hello"));
        QVERIFY(!p.next(&m));
    }
    void wireSplitAndBatched() {
        QByteArray a = SwayWire::encode(SwayWire::EventWindow, "{\"change\":\"focus\"}");
        a += SwayWire::encode(SwayWire::RunCommand, "[{\"success\":true}]");
        SwayWire::Parser p;
        SwayWire::Message m;
        for (int i = 0; i < a.size(); ++i) {          // one byte at a time
            p.feed(a.mid(i, 1));
            if (i < 13) QVERIFY(!p.next(&m));
        }
        QVERIFY(p.next(&m));
        QCOMPARE(m.type, quint32(SwayWire::EventWindow));
        QVERIFY(p.next(&m));
        QCOMPARE(m.type, quint32(SwayWire::RunCommand));
        QVERIFY(!p.next(&m));
        QVERIFY(!p.broken());
    }
    void wireRejectsBadMagic() {
        SwayWire::Parser p;
        p.feed(QByteArray("not-a-sway-message-at-all"));
        SwayWire::Message m;
        QVERIFY(!p.next(&m));
        QVERIFY(p.broken());
    }
    // ---- a request/subscribe round trip against a fake sway ----
    void swayIpcAgainstFakeSway() {
        QTemporaryDir dir;
        QVERIFY(dir.isValid());
        const QString path = dir.filePath(QStringLiteral("sway.sock"));
        QLocalServer fake;
        QVERIFY(fake.listen(path));
        SwayWire::Parser parser;
        connect(&fake, &QLocalServer::newConnection, this, [&] {
            QLocalSocket *s = fake.nextPendingConnection();
            connect(s, &QLocalSocket::readyRead, s, [s, &parser] {
                parser.feed(s->readAll());
                SwayWire::Message m;
                while (parser.next(&m)) {
                    if (m.type == SwayWire::Subscribe) {
                        s->write(SwayWire::encode(SwayWire::Subscribe, "{\"success\": true}"));
                        s->write(SwayWire::encode(SwayWire::EventWindow, "{\"change\":\"focus\",\"container\":{\"id\":4}}"));
                    } else {
                        s->write(SwayWire::encode(m.type, "reply:" + m.payload));
                    }
                }
            });
        });
        SwayIpc ipc;
        QVERIFY(ipc.connectToSway(path));
        QByteArray r1, r2;
        ipc.request(SwayWire::GetTree, "", [&](bool ok, const QByteArray &p) { QVERIFY(ok); r1 = p; });
        ipc.command(QStringLiteral("[con_id=4] focus"), [&](bool ok, const QByteArray &p) { QVERIFY(ok); r2 = p; });
        QTRY_VERIFY(!r2.isEmpty());
        QCOMPARE(r1, QByteArray("reply:"));                       // replies come back in request order
        QCOMPARE(r2, QByteArray("reply:[con_id=4] focus"));
        QSignalSpy spy(&ipc, &SwayIpc::event);
        ipc.subscribe({ QStringLiteral("window") });
        QTRY_COMPARE(spy.count(), 1);
        QCOMPARE(spy.at(0).at(0).toUInt(), quint32(SwayWire::EventWindow));
        QVERIFY(spy.at(0).at(1).toByteArray().contains("\"focus\""));
    }
    void swayIpcNotConnected() {
        SwayIpc ipc;
        bool called = false, ok = true;
        ipc.command(QStringLiteral("nop"), [&](bool o, const QByteArray &) { called = true; ok = o; });
        QVERIFY(called);
        QVERIFY(!ok);
        QVERIFY(!ipc.connectToSway(QStringLiteral("/nonexistent/sway.sock")));
    }
    // ---- the control socket ----
    void controlRoundTrip() {
        QTemporaryDir dir;
        QVERIFY(dir.isValid());
        const QString path = dir.filePath(QStringLiteral("taos-shell/overview.sock"));
        ControlServer server;
        QStringList seen;
        server.setHandler([&](const QStringList &w) {
            seen = w;
            if (w.value(0) == QLatin1String("status")) return QStringLiteral("ok {\"mode\":\"hidden\"}");
            if (w.value(0) == QLatin1String("bogus")) return QStringLiteral("error: unknown command");
            return QStringLiteral("ok");
        });
        QString err;
        QVERIFY2(server.listen(path, &err), qPrintable(err));
        // the socket is private to the user: mode 600, in a mode 700 directory
        struct stat st {};
        QCOMPARE(::stat(path.toLocal8Bit().constData(), &st), 0);
        QCOMPARE(int(st.st_mode & 0777), 0600);
        QCOMPARE(::stat(QFileInfo(path).absolutePath().toLocal8Bit().constData(), &st), 0);
        QCOMPARE(int(st.st_mode & 0777), 0700);

        // The client is blocking POSIX code (it runs before any Qt object in the real binary): run it
        // on a thread while this thread serves.
        const auto call = [&](const QStringList &words, int *rc, QString *reply) {
            std::thread t([&] { *rc = Control::send(path, words, reply); });
            while (!t.joinable()) {}
            QElapsedTimer tm;
            tm.start();
            std::atomic<bool> done { false };
            std::thread waiter([&] { t.join(); done = true; });
            while (!done && tm.elapsed() < 4000) QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
            waiter.join();
        };
        int rc = -1;
        QString reply;
        call({ QStringLiteral("switcher"), QStringLiteral("--reverse") }, &rc, &reply);
        QCOMPARE(rc, 0);
        QCOMPARE(reply, QStringLiteral("ok"));
        QCOMPARE(seen, QStringList({ "switcher", "--reverse" }));
        call({ QStringLiteral("status") }, &rc, &reply);
        QCOMPARE(rc, 0);
        QCOMPARE(reply, QStringLiteral("ok {\"mode\":\"hidden\"}"));
        call({ QStringLiteral("bogus") }, &rc, &reply);
        QCOMPARE(rc, 1);
        QVERIFY(reply.startsWith(QLatin1String("error")));
        call({ QStringLiteral("overview") }, &rc, &reply);
        QCOMPARE(rc, 0);
        QCOMPARE(seen, QStringList({ "overview" }));
    }
    void controlClientWithoutDaemon() {
        QString reply;
        QCOMPARE(Control::send(QStringLiteral("/nonexistent/overview.sock"), { QStringLiteral("ping") }, &reply), 2);
    }
    void secondDaemonIsRefused() {
        QTemporaryDir dir;
        QVERIFY(dir.isValid());
        const QString path = dir.filePath(QStringLiteral("taos-shell/overview.sock"));
        ControlServer a;
        a.setHandler([](const QStringList &) { return QStringLiteral("ok"); });
        QString err;
        QVERIFY(a.listen(path, &err));
        // `listen` pings the socket with a blocking call; the first server must answer meanwhile.
        ControlServer b;
        std::atomic<bool> done { false };
        bool ok = true;
        std::thread t([&] { ok = b.listen(path, &err); done = true; });
        QElapsedTimer tm;
        tm.start();
        while (!done && tm.elapsed() < 4000) QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
        t.join();
        QVERIFY(!ok);
        QVERIFY(err.contains(QLatin1String("already running")));
    }
};
QTEST_MAIN(TestIpc)
#include "test_ipc.moc"
