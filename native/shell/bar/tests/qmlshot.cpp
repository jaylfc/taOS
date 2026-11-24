// Dev tool, not a test: renders one of the bar's QML surfaces with FAKE data (a
// scripted NetworkManager and BlueZ on a private bus, a scripted sound server)
// and saves a PNG. Used to check the QML loads and to make labelled
// screenshots without any hardware.
//   qmlshot <net|audio|display|bar|osd> out.png [--light] [--size 1920x1080] [--reduce-motion]
//   display only: [--fixture two.json] [--confirm] [--xwayland] [--set-scale 1.1]
#include <QDBusConnection>
#include <QGuiApplication>
#include <QImage>
#include <QQmlContext>
#include <QQuickItem>
#include <QQuickView>
#include <QTimer>
#include <QtQml>

#include "audio.h"
#include "battery.h"
#include "bluezclient.h"
#include "clock.h"
#include "displaycontroller.h"
#include "drmfixture.h"
#include "fakesway.h"
#include <QTemporaryDir>
#include "fakeaudio.h"
#include "fakebluez.h"
#include "fakenm.h"
#include "launcher.h"
#include "menuhost.h"
#include "network.h"
#include "nmclient.h"
#include "theme.h"
#include "thememanager.h"

using namespace Fx;

static void fillAudio(FakeAudioBackend &b) {
    AudioState &s = b.st;
    s.connected = true;
    AudioCard hda; hda.index = 1; hda.name = "alsa_card.pci-hda"; hda.description = "Built-in Audio"; hda.bus = "pci"; hda.vendor = "Intel Corporation";
    hda.activeProfile = "output:analog-stereo+input:analog-stereo";
    hda.profiles << prof("off", "Off", 0, 0, 0) << prof("output:analog-stereo+input:analog-stereo", "Analog Stereo Duplex", 1, 1, 6565)
                 << prof("output:hdmi-stereo", "Digital Stereo (HDMI) Output", 1, 0, 5900, true);
    AudioCard usb; usb.index = 2; usb.name = "alsa_card.usb-Jabra"; usb.description = "Jabra Evolve 40"; usb.bus = "usb";
    usb.activeProfile = "output:analog-stereo+input:mono-fallback";
    AudioCard bt; bt.index = 3; bt.name = "bluez_card.AA"; bt.description = "Sony WH-1000XM4"; bt.bus = "bluetooth"; bt.bluetooth = true;
    bt.activeProfile = "a2dp-sink";
    bt.profiles << prof("a2dp-sink", "High Fidelity Playback (A2DP Sink)", 1, 0, 40) << prof("headset-head-unit", "Headset Head Unit (HSP/HFP)", 1, 1, 30);
    AudioCard nv; nv.index = 4; nv.name = "alsa_card.pci-nvidia"; nv.description = "GA104 High Definition Audio Controller"; nv.bus = "pci"; nv.vendor = "NVIDIA Corporation"; nv.activeProfile = "off";
    nv.profiles << prof("off", "Off", 0, 0, 0) << prof("output:hdmi-stereo", "Digital Stereo (HDMI) Output", 1, 0, 5900, false)
                << prof("output:hdmi-stereo-extra1", "Digital Stereo (HDMI 2) Output", 1, 0, 5800, true)
                << prof("output:hdmi-stereo-extra2", "Digital Stereo (HDMI 3) Output", 1, 0, 5700, false)
                << prof("output:hdmi-stereo-extra3", "Digital Stereo (HDMI 4) Output", 1, 0, 5600, false);
    s.cards << hda << usb << bt << nv;
    AudioDevice sp = sink(1, "alsa_output.pci-hda", "Built-in Audio Analog Stereo", 1);
    sp.ports << AudioPort{"analog-output-speaker", "Speakers", "", 100, Avail::Yes} << AudioPort{"analog-output-headphones", "Headphones", "", 90, Avail::No}
             << AudioPort{"analog-output-lineout", "Line Out", "", 80, Avail::Yes};
    sp.activePort = "analog-output-speaker"; sp.volume = 41;
    AudioDevice u = sink(2, "alsa_output.usb-Jabra", "Jabra Evolve 40 Analog Stereo", 2); u.bus = "usb"; u.formFactor = "headset"; u.product = "Jabra Evolve 40";
    AudioDevice bd = sink(3, "bluez_output.AA", "Sony WH-1000XM4", 3); bd.bus = "bluetooth"; bd.formFactor = "headphone"; bd.volume = 72;
    s.sinks << sp << u << bd;
    s.defaultSink = "bluez_output.AA";
    AudioDevice mic = source(10, "alsa_input.pci-hda", "Built-in Audio Analog Stereo", 1);
    mic.ports << AudioPort{"analog-input-mic", "Microphone", "", 100, Avail::Yes} << AudioPort{"analog-input-front-mic", "Front Microphone", "", 90, Avail::No};
    mic.activePort = "analog-input-mic"; mic.volume = 66;
    AudioDevice um = source(11, "alsa_input.usb-Jabra", "Jabra Evolve 40 Mono", 2); um.bus = "usb"; um.formFactor = "headset"; um.product = "Jabra Evolve 40";
    s.sources << mic << um;
    s.defaultSource = "alsa_input.usb-Jabra";
}

int main(int argc, char **argv) {
    // --live: a real window on whatever compositor is running (for grim); otherwise offscreen, software, saved to a PNG.
    bool live = false;
    for (int i = 1; i < argc; ++i) live |= QByteArray(argv[i]) == "--live";
    if (!live) {
        qputenv("QT_QPA_PLATFORM", qgetenv("QT_QPA_PLATFORM").isEmpty() ? QByteArray("offscreen") : qgetenv("QT_QPA_PLATFORM"));
        if (qEnvironmentVariableIsEmpty("QT_QUICK_BACKEND")) qputenv("QT_QUICK_BACKEND", "software");
    }
    QGuiApplication app(argc, argv);
    const QStringList a = app.arguments();
    if (a.size() < 3) { fprintf(stderr, "usage: qmlshot net|audio|display|bar|osd out.png [--light] [--size WxH] [--reduce-motion]\n"); return 2; }
    const QString what = a.at(1), out = a.at(2);
    const bool light = a.contains("--light"), reduce = a.contains("--reduce-motion");
    QString fixture = QStringLiteral("owner.json");
    if (const int fi = a.indexOf("--fixture"); fi > 0 && fi + 1 < a.size()) fixture = a.at(fi + 1);
    QSize size(1920, 1080);
    const int si = a.indexOf("--size");
    if (si > 0 && si + 1 < a.size()) { const auto p = a.at(si + 1).split('x'); size = QSize(p.value(0).toInt(), p.value(1).toInt()); }
    const QString qmlDir = QStringLiteral(TAOS_BAR_QML_DIR);

    Theme theme;
    QString err;
    // The theme comes from TAOS_APPEARANCE (default: the person's file); --light pins the mode.
    ThemeManager::Options themeOpts;
    if (light) themeOpts.pinnedVariant = QStringLiteral("light");
    themeOpts.reduceMotion = reduce;
    themeOpts.transitionMs = 150;
    ThemeManager themes(&theme, themeOpts);
    if (!themes.start(&err)) {
        fprintf(stderr, "theme: %s\n", qPrintable(err));
        return 1;
    }
    PrivateBus bus;
    if (!bus.start()) { fprintf(stderr, "no dbus-daemon\n"); return 1; }
    auto fakeConn = bus.connect("fake"), clientConn = bus.connect("client");
    FakeNm nm(fakeConn);
    nm.addWifi(100);
    nm.addEthernet(100, true, 1000);
    const QString home = nm.addAp("Home", 80, true);
    nm.addAp("Neighbour", 55, true); nm.addAp("Cafe Open", 42, false); nm.addAp("Corner Shop", 20, true); nm.addAp("Guest", 71, false);
    nm.objs[WIFIDEV][WL]["ActiveAccessPoint"] = opath(home);
    nm.addSaved("Home");
    fakeConn.registerService("org.freedesktop.NetworkManager");
    auto fakeBzConn = bus.connect("fakebz");
    FakeBluez bz(fakeBzConn);
    bz.addDevice("01", "Keyboard K380", true, true, -50, 90);
    bz.addDevice("02", "Sony WH-1000XM4", true, false, -60, 70);
    bz.addDevice("03", "Pixel 8", false, false, -48);
    bz.addDevice("04", "JBL Flip 6", false, false, -66);
    fakeBzConn.registerService("org.bluez");
    NmClient nmc(clientConn);
    BluezClient bzc(clientConn);
    FakeAudioBackend ab;
    fillAudio(ab);
    Audio audio(&ab);
    MenuHost host(&nmc, &bzc, &audio);
    Clock clock; Battery battery; Launcher launcher; Network network;
    network.attachNm(&nmc);
    nmc.start(); bzc.start();
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Theme", &theme);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Themes", &themes);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Clock", &clock);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Network", &network);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Wifi", &nmc);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Bluetooth", &bzc);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "MenuHost", &host);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Audio", &audio);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Battery", &battery);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Launcher", &launcher);

    // Display: a scripted compositor (the owner's ultrawide by default) and a fake /sys/class/drm.
    QTemporaryDir dispDir;
    FakeSway fakeSway;
    if (!fakeSway.load(QStringLiteral(TAOS_DISPLAY_FIXTURES "/") + fixture)) { fprintf(stderr, "no fixture %s\n", qPrintable(fixture)); return 1; }
    if (a.contains("--xwayland")) { QFile t(QStringLiteral(TAOS_DISPLAY_FIXTURES "/tree-xwayland.json")); if (t.open(QIODevice::ReadOnly)) fakeSway.tree = t.readAll(); }
    const QString drmRoot = dispDir.path() + "/drm";
    addConnector(drmRoot, "HDMI-A-1", "3840x1200\n3840x1200\n3840x2160\n", makeEdid(1043, 326));
    addConnector(drmRoot, "eDP-1", "1920x1080\n", makeEdid(294, 165));
    addConnector(drmRoot, "DP-2", "3840x2160\n2560x1440\n", makeEdid(597, 336));
    DisplayController display(&fakeSway, DisplayConfigFile(dispDir.path() + "/20-display.conf"), drmRoot);
    display.setSettleMs(0);
    display.refresh();
    if (const int ss = a.indexOf("--set-scale"); ss > 0 && ss + 1 < a.size() && !display.rawOutputs().isEmpty())
        display.setScale(display.rawOutputs().first().name, a.at(ss + 1).toDouble());
    if (a.contains("--confirm") && !display.rawOutputs().isEmpty()) {
        const DisplayOutput &o = display.rawOutputs().first();
        display.setRefresh(o.name, ratesAt(o, chooseNative(o).resolution).first());
    }
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Display", &display);

    QQuickView v;
    v.setColor(QColor("#2b3a55"));  // stand-in wallpaper: the real one sits behind the menu
    v.setResizeMode(QQuickView::SizeRootObjectToView);
    const QString file = what == "net" ? "NetMenu.qml" : what == "audio" ? "AudioMenu.qml" : what == "display" ? "DisplayMenu.qml" : what == "osd" ? "Osd.qml" : "Bar.qml";
    if (what == "osd") size = QSize(260, 84);
    if (what == "bar") size.setHeight(40);
    v.resize(size);
    if (what == "net" || what == "audio" || what == "display") v.setInitialProperties({{"barHeight", 40}, {"anchorRight", size.width() - 80.0}});
    else if (what == "bar") v.setInitialProperties({{"barHeight", 40}, {"outputName", "FAKE-1"}});
    v.setSource(QUrl::fromLocalFile(qmlDir + "/" + file));
    if (v.status() != QQuickView::Ready) {
        for (const auto &e : v.errors()) fprintf(stderr, "qml: %s\n", qPrintable(e.toString()));
        return 1;
    }
    if (what == "audio") { host.setOpen(true, "audio"); QTimer::singleShot(300, [&] { emit ab.meterLevel(0.55); }); }
    if (what == "net") host.setOpen(true, "network");
    if (what == "display") host.setOpen(true, "display");
    if (live) v.showFullScreen(); else v.show();
    if (what == "osd") QTimer::singleShot(100, [&] { QMetaObject::invokeMethod(static_cast<QObject *>(v.rootObject()), "flash", Q_ARG(QVariant, QString("volume")), Q_ARG(QVariant, 72), Q_ARG(QVariant, false)); });
    if (live) {
        QTimer::singleShot(12000, [&] { app.quit(); });  // long enough for grim; the caller captures
        return app.exec();
    }
    QTimer::singleShot(1400, [&] {
        const QImage img = v.grabWindow();
        if (!img.save(out)) { fprintf(stderr, "cannot save %s\n", qPrintable(out)); app.exit(1); return; }
        printf("saved %s (%dx%d)\n", qPrintable(out), img.width(), img.height());
        app.quit();
    });
    return app.exec();
}
