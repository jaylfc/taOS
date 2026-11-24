// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "audio.h"
#include "fakeaudio.h"

using namespace Fx;

class TestAudio : public QObject {
    Q_OBJECT
    static void basic(FakeAudioBackend &b) {
        b.st.connected = true;
        b.st.defaultSink = "a"; b.st.defaultSource = "mic";
        b.st.sinks << sink(1, "a", "Speakers A") << sink(2, "b", "Speakers B");
        b.st.sources << source(10, "mic", "Mic");
        b.st.streams << AudioStream{100, 1, "Music"};
    }
private slots:
    void keysStepFiveAndCapAtOneHundred() {
        FakeAudioBackend b; basic(b);
        Audio a(&b);
        QSignalSpy flash(&a, &Audio::flash);
        b.st.sinks[0].volume = 98;
        a.stepVolume(Audio::kStep);
        QCOMPARE(b.st.sinks[0].volume, 100);  // not 103
        a.stepVolume(Audio::kStep);
        QCOMPARE(b.st.sinks[0].volume, 100);
        a.stepVolume(-Audio::kStep);
        QCOMPARE(b.st.sinks[0].volume, 95);
        QCOMPARE(flash.last().at(1).toInt(), 95);
        b.st.sinks[0].volume = 120;  // already boosted elsewhere: the key does not touch it going up
        b.calls.clear();
        a.stepVolume(Audio::kStep);
        QVERIFY(!b.calls.join('|').contains("volume"));
    }
    void volumeUpUnmutes() {
        FakeAudioBackend b; basic(b);
        Audio a(&b);
        b.st.sinks[0].mute = true;
        a.stepVolume(5);
        QVERIFY(!b.st.sinks[0].mute);
        a.toggleMute();
        QVERIFY(b.st.sinks[0].mute);
        a.toggleMicMute();
        QVERIFY(b.st.sources[0].mute);
    }
    void keysWithoutADeviceSayNoDevice() {
        FakeAudioBackend b; b.st.connected = true;
        Audio a(&b);
        QSignalSpy flash(&a, &Audio::flash);
        a.stepVolume(5);
        QCOMPARE(flash.last().at(1).toInt(), -1);
        QCOMPARE(a.level(), QStringLiteral("none"));
        QCOMPARE(a.description(), QStringLiteral("No sound output"));
    }
    void backendMissing() {
        FakeAudioBackend b;
        Audio a(&b);
        QVERIFY(!a.available());
        QCOMPARE(a.message(), QStringLiteral("Sound is not available on this computer"));
    }
    void volumeAndMuteRoundTrip() {
        FakeAudioBackend b; basic(b);
        Audio a(&b);
        QCOMPARE(a.percent(), 50);
        a.setPercent(130);  // the slider cannot ask for more than 100
        QCOMPARE(a.percent(), 100);
        a.setMuted(true);
        QVERIFY(a.muted());
        QCOMPARE(a.level(), QStringLiteral("muted"));
        a.setMuted(false);
        a.setPercent(20);
        QCOMPARE(a.level(), QStringLiteral("low"));
        QCOMPARE(a.description(), QStringLiteral("Volume 20 percent"));
    }
    void choosingAnOutputSetsDefaultAndPort() {
        FakeAudioBackend b; basic(b);
        b.st.sinks[1].ports << AudioPort{"analog-output-lineout", "Line Out", "", 1, Avail::Yes}
                            << AudioPort{"analog-output-headphones", "Headphones", "", 2, Avail::Yes};
        b.st.sinks[1].activePort = "analog-output-lineout";
        Audio a(&b);
        QString id;
        for (const auto &c : a.outputs()->rows()) if (c.portName == "analog-output-headphones") id = c.id;
        QVERIFY(!id.isEmpty());
        QSignalSpy ann(&a, &Audio::announcement);
        a.selectOutput(id);
        QVERIFY(b.calls.join('|').contains("port sink 2 analog-output-headphones"));
        QCOMPARE(b.st.defaultSink, QStringLiteral("b"));
        QVERIFY(ann.count() >= 1);
    }
    void choosingAnOutputOnAnotherProfileSwitchesProfileThenDefault() {
        FakeAudioBackend b; basic(b);
        AudioCard c; c.index = 3; c.name = "alsa_card.hda"; c.description = "Built-in Audio"; c.bus = "pci"; c.activeProfile = "output:analog-stereo";
        c.profiles << prof("off", "Off", 0, 0, 0) << prof("output:analog-stereo", "Analog Stereo Output", 1, 0, 10)
                   << prof("output:hdmi-stereo", "Digital Stereo (HDMI) Output", 1, 0, 5, true);
        b.st.cards << c;
        Audio a(&b);
        QString id;
        for (const auto &ch : a.outputs()->rows()) if (!ch.profileName.isEmpty()) id = ch.id;
        QVERIFY(!id.isEmpty());
        a.selectOutput(id);
        QCOMPARE(b.calls.last(), QStringLiteral("profile 3 output:hdmi-stereo"));
        QCOMPARE(b.st.defaultSink, QStringLiteral("a"));  // nothing yet: the new device does not exist
        // The server switched the profile and the new sink appeared.
        b.st.cards[0].activeProfile = "output:hdmi-stereo";
        b.st.sinks << sink(9, "hdmi_sink", "Built-in Audio Digital Stereo (HDMI)", 3);
        emit b.stateChanged();
        QCOMPARE(b.st.defaultSink, QStringLiteral("hdmi_sink"));  // and now it is the default (which moves playing streams)
        QVERIFY(b.calls.join('|').contains("default sink hdmi_sink"));
    }
    void bluetoothHeadsetProfile() {
        FakeAudioBackend b; basic(b);
        AudioCard bt; bt.index = 4; bt.name = "bluez_card.AA"; bt.description = "Sony"; bt.bus = "bluetooth"; bt.bluetooth = true; bt.activeProfile = "a2dp-sink";
        bt.profiles << prof("a2dp-sink", "A2DP", 1, 0, 40) << prof("headset-head-unit", "HSP/HFP", 1, 1, 30);
        b.st.cards << bt;
        b.st.sinks[0].card = 4; b.st.sinks[0].bus = "bluetooth";
        Audio a(&b);
        QCOMPARE(a.outputProfiles()->rowCount(), 2);
        a.selectProfile(4, "headset-head-unit", true);
        QCOMPARE(b.calls.last(), QStringLiteral("profile 4 headset-head-unit"));
    }
    void meterRunsOnlyWhileTheMenuIsOpen() {
        FakeAudioBackend b; basic(b);
        Audio a(&b);
        QCOMPARE(b.meters, 0);
        a.setMenuOpen(true);
        QCOMPARE(b.meters, 1);
        QVERIFY(b.calls.join('|').contains("meter-start mic"));
        // The default input changes while open: the meter follows.
        b.st.sources << source(11, "mic2", "USB Mic");
        b.st.defaultSource = "mic2";
        emit b.stateChanged();
        QVERIFY(b.calls.join('|').contains("meter-start mic2"));
        QCOMPARE(b.meters, 1);
        emit b.meterLevel(0.6);
        QCOMPARE(a.micLevel(), 0.6);
        a.setMenuOpen(false);
        QCOMPARE(b.meters, 0);
        emit b.meterLevel(0.9);  // late sample after closing
        QCOMPARE(a.micLevel(), 0.0);
    }
    void defaultChangedByTheSystemIsAnnounced() {
        FakeAudioBackend b; basic(b);
        Audio a(&b);
        QSignalSpy ann(&a, &Audio::announcement);
        b.st.defaultSink = "b";  // a headset was plugged in and became the default
        emit b.stateChanged();
        QCOMPARE(ann.count(), 1);
        QVERIFY(ann.at(0).at(0).toString().startsWith("Sound output:"));
        emit b.stateChanged();  // nothing changed: silent
        QCOMPARE(ann.count(), 1);
    }
    void failureIsPlainWords() {
        FakeAudioBackend b; basic(b);
        Audio a(&b);
        emit b.failed("switch profile: No such entity");
        QVERIFY(a.errorText().startsWith("Sound did not change"));
    }
};
QTEST_MAIN(TestAudio)
#include "test_audio.moc"
