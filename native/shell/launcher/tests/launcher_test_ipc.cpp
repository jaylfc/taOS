#include <QtTest>
#include <QLocalServer>
#include <atomic>
#include <thread>

#include "ipc.h"

using namespace taos::launcher;

class TestIpc : public QObject {
    Q_OBJECT
private slots:
    void socketPathUsesRuntimeDir() {
        qputenv("XDG_RUNTIME_DIR", QByteArray("/tmp"));
        QCOMPARE(socketPath(), QString("/tmp/taos/launcher.sock"));
    }
    void toggleRoundTrip() {
        QTemporaryDir d;
        const QString path = d.path() + "/taos/launcher.sock";
        Server s;
        int toggles = 0;
        s.setHandler([&](const QStringList &w) {
            if (w.value(0) == "ping") return QString("ok pong");
            if (w.value(0) == "toggle") { ++toggles; return QString("ok shown"); }
            return QString("error: unknown command");
        });
        QString err;
        QVERIFY2(s.listen(path, &err), qPrintable(err));
        QString reply;
        int rc = -1;
        // sendCommand blocks, so it runs in a thread while this one serves the socket
        std::atomic<bool> done{false};
        std::thread t([&] { rc = sendCommand(path, { "toggle" }, &reply); done = true; });
        QTRY_VERIFY_WITH_TIMEOUT(done.load(), 3000);
        QCOMPARE(toggles, 1);
        t.join();
        QCOMPARE(rc, 0);
        QCOMPARE(reply, QString("ok shown"));
        done = false;
        std::thread t2([&] { rc = sendCommand(path, { "bogus" }, &reply); done = true; });
        QTRY_VERIFY_WITH_TIMEOUT(done.load(), 3000);
        t2.join();
        QCOMPARE(rc, 1);
    }
    void secondInstanceIsRefused() {
        QTemporaryDir d;
        const QString path = d.path() + "/launcher.sock";
        Server first;
        first.setHandler([](const QStringList &) { return QString("ok pong"); });
        QString err;
        QVERIFY(first.listen(path, &err));
        std::atomic<bool> finished{false};
        bool refused = false;
        QString err2;
        // the second daemon's startup check pings the first, which must be served meanwhile
        std::thread t([&] {
            Server second;
            refused = !second.listen(path, &err2);
            finished = true;
        });
        QTRY_VERIFY_WITH_TIMEOUT(finished.load(), 3000);
        t.join();
        QVERIFY(refused);
        QVERIFY(err2.contains("already running"));
    }
    void staleSocketIsReplaced() {
        QTemporaryDir d;
        const QString path = d.path() + "/launcher.sock";
        {   // leave a socket file behind with nobody serving it
            QLocalServer s;
            QVERIFY(s.listen(path));
            s.close();
        }
        QFile::remove(path);
        QFile f(path);
        QVERIFY(f.open(QIODevice::WriteOnly));   // a plain stale file at the path
        f.close();
        Server s;
        QString err;
        QVERIFY2(s.listen(path, &err), qPrintable(err));
    }
    void noDaemonMeansExit2() {
        QString reply;
        QCOMPARE(sendCommand("/nonexistent/dir/launcher.sock", { "toggle" }, &reply), 2);
    }
};
QTEST_MAIN(TestIpc)
#include "launcher_test_ipc.moc"
