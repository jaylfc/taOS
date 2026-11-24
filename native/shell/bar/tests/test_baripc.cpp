// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "audio.h"
#include "baripc.h"
#include "fakeaudio.h"

using namespace Fx;

class TestBarIpc : public QObject {
    Q_OBJECT
private slots:
    void commandsReachTheAudioController() {
        FakeAudioBackend b;
        b.st.connected = true; b.st.defaultSink = "a"; b.st.defaultSource = "m";
        b.st.sinks << sink(1, "a", "A"); b.st.sources << source(2, "m", "M");
        Audio audio(&b);
        QSignalSpy flash(&audio, &Audio::flash);
        QTemporaryDir d;
        const QString sock = d.filePath("bar.sock");
        BarIpc ipc(&audio);
        QVERIFY(ipc.listen(sock));
        // The client blocks on the reply, so it runs in a thread while this one serves it.
        auto run = [&](const QStringList &args) {
            int rc = -1;
            QString msg;
            QThread *t = QThread::create([&] { rc = BarIpc::send(args, sock, &msg); });
            t->start();
            while (!t->isFinished()) QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
            t->wait();
            delete t;
            return qMakePair(rc, msg);
        };
        QCOMPARE(run({"volume", "up"}).first, 0);
        QCOMPARE(b.st.sinks[0].volume, 55);
        QCOMPARE(flash.last().at(0).toString(), QStringLiteral("volume"));
        QCOMPARE(flash.last().at(1).toInt(), 55);
        QCOMPARE(run({"volume", "down"}).first, 0);
        QCOMPARE(b.st.sinks[0].volume, 50);
        QCOMPARE(run({"volume", "mute"}).first, 0);
        QVERIFY(b.st.sinks[0].mute);
        QCOMPARE(run({"volume", "mic-mute"}).first, 0);
        QVERIFY(b.st.sources[0].mute);
        QCOMPARE(run({"mic-mute"}).first, 0);
        QVERIFY(!b.st.sources[0].mute);
        const auto bad = run({"volume", "sideways"});
        QCOMPARE(bad.first, 1);
        QVERIFY(bad.second.startsWith("error:"));
    }
    void notRunningIsReportedPlainly() {
        QTemporaryDir d;
        QString msg;
        QCOMPARE(BarIpc::send({"volume", "up"}, d.filePath("none.sock"), &msg), 1);
        QCOMPARE(msg, QStringLiteral("taos-bar is not running"));
    }
};
QTEST_MAIN(TestBarIpc)
#include "test_baripc.moc"
