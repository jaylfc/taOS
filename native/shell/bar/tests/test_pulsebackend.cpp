// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "audio.h"
#include "pulsebackend.h"
#include "pwharness.h"

// Live tests against a private PipeWire (with WirePlumber and pipewire-pulse)
// started here. They are skipped, loudly, when those tools are not on PATH.
class TestPulseBackend : public QObject {
    Q_OBJECT
    std::unique_ptr<PrivatePipeWire> pw;
    std::unique_ptr<PulseBackend> be;
    std::unique_ptr<Audio> audio;

    static const AudioDevice *dev(const AudioState &s, bool sink, const QString &name) {
        for (const AudioDevice &d : sink ? s.sinks : s.sources) if (d.name == name) return &d;
        return nullptr;
    }
private slots:
    void initTestCase() {
        if (!PrivatePipeWire::toolsPresent()) QSKIP("pipewire, wireplumber, pipewire-pulse, pw-cli, pw-dump and pacat are not all on PATH");
        pw.reset(new PrivatePipeWire);
        QVERIFY2(pw->start(), "private PipeWire did not start");
        QVERIFY(pw->createNode("tt-sink-a", "Test Speakers A", false) > 0);
        QVERIFY(pw->createNode("tt-sink-b", "Test Speakers B", false) > 0);
        pw->startVirtualMic("tt-mic", "Test Microphone");
        be.reset(new PulseBackend(pw->server()));
        audio.reset(new Audio(be.get()));
        QTRY_VERIFY_WITH_TIMEOUT(be->connected() && dev(be->state(), true, "tt-sink-b") && dev(be->state(), false, "tt-mic"), 15000);
    }
    void cleanupTestCase() {
        audio.reset();
        be.reset();
        pw.reset();
    }

    void listsVirtualDevicesAndHidesMonitors() {
        QStringList out, in;
        for (const auto &c : audio->outputs()->rows()) out << c.deviceName;
        for (const auto &c : audio->inputs()->rows()) in << c.deviceName;
        QVERIFY(out.contains("tt-sink-a") && out.contains("tt-sink-b"));
        QVERIFY(in.contains("tt-mic"));
        for (const QString &n : in) QVERIFY2(!n.endsWith(".monitor"), qPrintable(n));  // monitors are not microphones
        QVERIFY(audio->available());
    }
    void volumeAndMuteRoundTrip() {
        const AudioDevice *d = dev(be->state(), true, "tt-sink-a");
        QVERIFY(d);
        be->setDefault(true, "tt-sink-a");
        QTRY_COMPARE_WITH_TIMEOUT(be->state().defaultSink, QStringLiteral("tt-sink-a"), 5000);
        // A virtual sink only applies volume while something plays through it (hardware sinks do not care).
        std::unique_ptr<QProcess> player(pw->startPlayer());
        QTRY_VERIFY_WITH_TIMEOUT(!be->state().streams.isEmpty(), 8000);
        QTest::qWait(500);
        audio->setPercent(37);
        QTRY_COMPARE_WITH_TIMEOUT(audio->percent(), 37, 5000);
        audio->setMuted(true);
        QTRY_VERIFY_WITH_TIMEOUT(audio->muted(), 5000);
        QCOMPARE(audio->level(), QStringLiteral("muted"));
        audio->setMuted(false);
        QTRY_VERIFY_WITH_TIMEOUT(!audio->muted(), 5000);
        audio->setPercent(100);
        QTRY_COMPARE_WITH_TIMEOUT(audio->percent(), 100, 5000);
        audio->stepVolume(5);  // capped
        QTest::qWait(300);
        QCOMPARE(audio->percent(), 100);
        audio->stepVolume(-5);
        QTRY_COMPARE_WITH_TIMEOUT(audio->percent(), 95, 5000);
        player->terminate();
        player->waitForFinished(3000);
    }
    void changingTheDefaultMovesAPlayingStream() {
        be->setDefault(true, "tt-sink-a");
        QTRY_COMPARE_WITH_TIMEOUT(be->state().defaultSink, QStringLiteral("tt-sink-a"), 5000);
        std::unique_ptr<QProcess> player(pw->startPlayer());
        QVERIFY(player->state() == QProcess::Running);
        QTRY_VERIFY_WITH_TIMEOUT(!be->state().streams.isEmpty(), 8000);
        const quint32 a = dev(be->state(), true, "tt-sink-a")->index, b = dev(be->state(), true, "tt-sink-b")->index;
        QTRY_COMPARE_WITH_TIMEOUT(be->state().streams.first().device, a, 8000);
        QString id;
        for (const auto &c : audio->outputs()->rows()) if (c.deviceName == "tt-sink-b") id = c.id;
        audio->selectOutput(id);
        QTRY_COMPARE_WITH_TIMEOUT(be->state().defaultSink, QStringLiteral("tt-sink-b"), 5000);
        QTRY_COMPARE_WITH_TIMEOUT(be->state().streams.first().device, b, 8000);  // the stream followed
        for (const auto &c : audio->outputs()->rows()) QCOMPARE(c.active, c.deviceName == "tt-sink-b");
        player->terminate();
        player->waitForFinished(3000);
    }
    void devicesAppearAndDisappearLive() {
        QSignalSpy changed(be.get(), &AudioBackend::stateChanged);
        const int id = pw->createNode("tt-sink-c", "Test Headset", false);
        QVERIFY(id > 0);
        QTRY_VERIFY_WITH_TIMEOUT(dev(be->state(), true, "tt-sink-c") != nullptr, 8000);
        bool listed = false;
        for (const auto &c : audio->outputs()->rows()) listed |= c.deviceName == "tt-sink-c";
        QVERIFY(listed);
        QVERIFY(changed.count() >= 1);
        pw->destroyNode(id);
        QTRY_VERIFY_WITH_TIMEOUT(dev(be->state(), true, "tt-sink-c") == nullptr, 8000);
        for (const auto &c : audio->outputs()->rows()) QVERIFY(c.deviceName != "tt-sink-c");
    }
    void profileSwitchOnAMissingCardFailsInPlainWords() {
        // A private PipeWire has no sound card with profiles, so this checks the call is made and its refusal reported.
        QSignalSpy failed(be.get(), &AudioBackend::failed);
        be->setCardProfile(4242, "output:hdmi-stereo");
        QTRY_VERIFY_WITH_TIMEOUT(failed.count() == 1, 5000);
        QVERIFY(failed.at(0).at(0).toString().startsWith("switch profile"));
        QVERIFY(audio->errorText().startsWith("Sound did not change"));
    }
    void micMeterRunsOnlyWhileOpen() {
        be->setDefault(false, "tt-mic");
        QTRY_COMPARE_WITH_TIMEOUT(be->state().defaultSource, QStringLiteral("tt-mic"), 5000);
        QSignalSpy level(be.get(), &AudioBackend::meterLevel);
        QTest::qWait(600);
        QCOMPARE(level.count(), 0);  // closed: no meter stream, no samples
        audio->setMenuOpen(true);
        QTRY_VERIFY_WITH_TIMEOUT(level.count() > 0, 6000);
        audio->setMenuOpen(false);
        QTest::qWait(500);
        const int n = level.count();
        QTest::qWait(800);
        QCOMPARE(level.count(), n);  // stopped
    }
    void nothingAsksTheServerWhileIdle() {
        // Let the server's own late events (idle sinks suspending after the last test's streams) pass.
        for (int i = 0, last = -1, quiet = 0; i < 250 && quiet < 75; ++i) {
            QTest::qWait(100);
            quiet = be->queryCount() == last ? quiet + 1 : 0;
            last = be->queryCount();
        }
        const int q = be->queryCount();
        QSignalSpy changed(be.get(), &AudioBackend::stateChanged);
        QTest::qWait(2500);
        QCOMPARE(be->queryCount(), q);
        QCOMPARE(changed.count(), 0);
        QVERIFY(!be->retryTimerActive());
        const auto timers = be->findChildren<QTimer *>();
        for (QTimer *t : timers) QVERIFY2(!t->isActive(), "a timer is running while idle");
    }
};

class TestNoServer : public QObject {
    Q_OBJECT
private slots:
    void missingServerSaysSoAndRetriesQuietly() {
        QTemporaryDir d;
        PulseBackend be(QStringLiteral("unix:") + d.filePath("nothing"));
        Audio a(&be);
        QTRY_VERIFY_WITH_TIMEOUT(be.retryTimerActive(), 5000);
        QVERIFY(!a.available());
        QCOMPARE(a.message(), QStringLiteral("Sound is not available on this computer"));
        QCOMPARE(a.description(), QStringLiteral("Sound is not available"));
    }
};

int main(int argc, char **argv) {
    QGuiApplication app(argc, argv);
    int rc = 0;
    { TestPulseBackend t; rc |= QTest::qExec(&t, argc, argv); }
    { TestNoServer t; rc |= QTest::qExec(&t, argc, argv); }
    return rc;
}
#include "test_pulsebackend.moc"
