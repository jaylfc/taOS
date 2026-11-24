// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QtTest>

#include "audiotypes.h"
#include "fakeaudio.h"

using namespace Fx;

static AudioCard nvidia() {
    AudioCard c;
    c.index = 1; c.name = "alsa_card.pci-0000_01_00.1"; c.description = "GA104 High Definition Audio Controller"; c.bus = "pci";
    c.activeProfile = "off";
    // Four HDMI outputs on the card; one monitor is plugged in.
    c.profiles << prof("off", "Off", 0, 0, 0)
               << prof("output:hdmi-stereo", "Digital Stereo (HDMI) Output", 1, 0, 5900, false)
               << prof("output:hdmi-stereo-extra1", "Digital Stereo (HDMI 2) Output", 1, 0, 5800, true)
               << prof("output:hdmi-stereo-extra2", "Digital Stereo (HDMI 3) Output", 1, 0, 5700, false)
               << prof("output:hdmi-stereo-extra3", "Digital Stereo (HDMI 4) Output", 1, 0, 5600, false);
    return c;
}

class TestAudioState : public QObject {
    Q_OBJECT
private slots:
    void unavailableHdmiOutputsAreHidden() {
        AudioState s; s.connected = true;
        s.cards << nvidia();
        const auto out = AudioNames::choices(s, true);
        QCOMPARE(out.size(), 1);  // three of the four HDMI outputs have nothing plugged in
        QCOMPARE(out.at(0).title, QStringLiteral("Monitor speakers (HDMI 2)"));
        QCOMPARE(out.at(0).profileName, QStringLiteral("output:hdmi-stereo-extra1"));
        QCOMPARE(out.at(0).icon, QStringLiteral("hdmi"));
        QCOMPARE(out.at(0).subtitle, QStringLiteral("NVIDIA graphics card"));  // never "GA104 High Definition Audio Controller"
    }
    void unavailablePortsOnALiveSinkAreHidden() {
        AudioState s; s.connected = true; s.defaultSink = "hda";
        AudioCard c; c.index = 2; c.name = "alsa_card.pci-hda"; c.description = "Built-in Audio"; c.bus = "pci"; c.activeProfile = "output:analog-stereo+input:analog-stereo";
        s.cards << c;
        AudioDevice d = sink(5, "hda", "Built-in Audio Analog Stereo", 2);
        d.ports << AudioPort{"analog-output-speaker", "Speakers", "", 100, Avail::Yes}
                << AudioPort{"analog-output-headphones", "Headphones", "", 90, Avail::No}
                << AudioPort{"analog-output-lineout", "Line Out", "", 80, Avail::Yes};
        d.activePort = "analog-output-speaker";
        s.sinks << d;
        const auto out = AudioNames::choices(s, true);
        QStringList titles;
        for (const auto &c2 : out) titles << c2.title;
        QVERIFY(!titles.contains("Headphones (analog jack)"));  // not plugged in
        QVERIFY(titles.contains("Speakers"));
        QVERIFY(titles.contains("Line out (rear jack)"));
        for (const auto &c3 : out) QCOMPARE(c3.subtitle, QStringLiteral("Built-in audio"));
        for (const auto &c2 : out) QCOMPARE(c2.active, c2.portName == "analog-output-speaker");
        // Plug the headphones in: the port appears.
        s.sinks[0].ports[1].available = Avail::Yes;
        QCOMPARE(AudioNames::choices(s, true).size(), 3);
    }
    void usbHeadsetAndBluetoothHeadphonesGetPeopleNames() {
        AudioState s; s.connected = true; s.defaultSink = "bluez_output.AA";
        AudioCard usb; usb.index = 3; usb.name = "alsa_card.usb-Jabra"; usb.description = "Jabra Evolve 40"; usb.bus = "usb"; usb.activeProfile = "output:analog-stereo+input:mono-fallback";
        AudioCard bt; bt.index = 4; bt.name = "bluez_card.AA"; bt.description = "Sony WH-1000XM4"; bt.bus = "bluetooth"; bt.bluetooth = true; bt.activeProfile = "a2dp-sink";
        s.cards << usb << bt;
        AudioDevice u = sink(6, "alsa_output.usb-Jabra", "Jabra Evolve 40 Analog Stereo", 3); u.bus = "usb"; u.formFactor = "headset"; u.product = "Jabra Evolve 40";
        AudioDevice b = sink(7, "bluez_output.AA", "Sony WH-1000XM4", 4); b.bus = "bluetooth"; b.formFactor = "headphone";
        s.sinks << u << b;
        const auto out = AudioNames::choices(s, true);
        QCOMPARE(out.size(), 2);
        QCOMPARE(out.at(0).title, QStringLiteral("Headphones (Sony WH-1000XM4)"));  // Bluetooth first
        QCOMPARE(out.at(0).subtitle, QStringLiteral("Bluetooth"));
        QVERIFY(out.at(0).active);
        QCOMPARE(out.at(1).title, QStringLiteral("USB headset"));
        QCOMPARE(out.at(1).subtitle, QStringLiteral("Jabra Evolve 40"));
        QVERIFY(!out.at(1).active);
    }
    void monitorSourcesAreNotMicrophones() {
        AudioState s; s.connected = true; s.defaultSource = "mic";
        AudioDevice m = source(1, "alsa_output.x.monitor", "Monitor of Speakers"); m.monitor = true;
        AudioDevice mic = source(2, "mic", "Blue Yeti"); mic.bus = "usb"; mic.formFactor = "microphone"; mic.product = "Blue Yeti";
        s.sources << m << mic;
        const auto in = AudioNames::choices(s, false);
        QCOMPARE(in.size(), 1);
        QCOMPARE(in.at(0).title, QStringLiteral("USB microphone"));
        QVERIFY(in.at(0).active);
    }
    void analogVersusHdmiOnOneCard() {
        AudioState s; s.connected = true; s.defaultSink = "hda";
        AudioCard c; c.index = 2; c.name = "alsa_card.pci-hda"; c.description = "Built-in Audio"; c.bus = "pci";
        c.activeProfile = "output:analog-stereo+input:analog-stereo";
        c.profiles << prof("off", "Off", 0, 0, 0)
                   << prof("output:analog-stereo+input:analog-stereo", "Analog Stereo Duplex", 1, 1, 6565)
                   << prof("output:analog-stereo", "Analog Stereo Output", 1, 0, 6500)
                   << prof("output:hdmi-stereo", "Digital Stereo (HDMI) Output", 1, 0, 5900, true)
                   << prof("output:hdmi-stereo+input:analog-stereo", "Digital Stereo (HDMI) Output + Analog Stereo Input", 1, 1, 5965, true);
        s.cards << c;
        AudioDevice d = sink(5, "hda", "Built-in Audio Analog Stereo", 2);
        d.ports << AudioPort{"analog-output-speaker", "Speakers", "", 100, Avail::Yes};
        d.activePort = "analog-output-speaker";
        s.sinks << d;
        const auto out = AudioNames::choices(s, true);
        QCOMPARE(out.size(), 2);  // speakers (current) and the HDMI profile, once
        QCOMPARE(out.at(0).title, QStringLiteral("Monitor speakers (HDMI)"));
        QCOMPARE(out.at(0).profileName.startsWith("output:hdmi-stereo"), true);
        QCOMPARE(out.at(1).title, QStringLiteral("Speakers"));
        QVERIFY(out.at(1).active);
        const auto picker = AudioNames::profileChoices(s, true);
        QVERIFY(picker.size() >= 2);
        bool anyActive = false;
        for (const auto &p : picker) anyActive |= p.active;
        QVERIFY(anyActive);
    }
    void bluetoothProfilePicker() {
        AudioState s; s.connected = true; s.defaultSink = "bluez_output.AA";
        AudioCard bt; bt.index = 4; bt.name = "bluez_card.AA"; bt.description = "Sony WH-1000XM4"; bt.bus = "bluetooth"; bt.bluetooth = true;
        bt.activeProfile = "a2dp-sink";
        bt.profiles << prof("off", "Off", 0, 0, 0) << prof("a2dp-sink", "High Fidelity Playback (A2DP Sink)", 1, 0, 40)
                    << prof("headset-head-unit", "Headset Head Unit (HSP/HFP)", 1, 1, 30);
        s.cards << bt;
        AudioDevice b = sink(7, "bluez_output.AA", "Sony WH-1000XM4", 4); b.bus = "bluetooth";
        s.sinks << b;
        QString title;
        const auto p = AudioNames::profileChoices(s, true, &title);
        QCOMPARE(p.size(), 2);
        QCOMPARE(p.at(0).label, QStringLiteral("High quality music"));
        QVERIFY(p.at(0).active);
        QCOMPARE(p.at(1).label, QStringLiteral("Headset with microphone"));
        QVERIFY(!p.at(1).active);
        QCOMPARE(p.at(1).name, QStringLiteral("headset-head-unit"));
        QCOMPARE(title, QStringLiteral("Headphones (Sony WH-1000XM4)"));
        // Bluetooth profiles are not output rows of their own.
        QCOMPARE(AudioNames::choices(s, true).size(), 1);
    }
    void noPickerWhenThereIsNothingToChoose() {
        AudioState s; s.connected = true; s.defaultSink = "x";
        AudioCard c; c.index = 9; c.name = "alsa_card.usb"; c.description = "USB DAC"; c.bus = "usb"; c.activeProfile = "output:analog-stereo";
        c.profiles << prof("off", "Off", 0, 0, 0) << prof("output:analog-stereo", "Analog Stereo Output", 1, 0, 6500);
        s.cards << c;
        s.sinks << sink(1, "x", "USB DAC Analog Stereo", 9);
        QVERIFY(AudioNames::profileChoices(s, true).isEmpty());
    }
};
QTEST_APPLESS_MAIN(TestAudioState)
#include "test_audiostate.moc"
