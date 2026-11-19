#include <LayerShellQt/Window>
#include <QCommandLineParser>
#include <QGuiApplication>
#include <QQmlContext>
#include <QQmlEngine>
#include <QDBusConnection>
#include <QSocketNotifier>
#include <QStandardPaths>
#include <QtQml>
#include <csignal>
#include <cstdio>
#include <unistd.h>

#include "barmanager.h"
#include "battery.h"
#include "clock.h"
#include "bluezclient.h"
#include "launcher.h"
#include "menuhost.h"
#include "nmclient.h"
#include "network.h"
#include "theme.h"
#include "volume.h"

static int g_sigPipe[2] = {-1, -1};
static void onSignal(int) {
    const char c = 1;
    const ssize_t r = write(g_sigPipe[1], &c, 1);
    (void)r;
}

int main(int argc, char **argv) {
    QStringList args;
    for (int i = 0; i < argc; ++i) args << QString::fromLocal8Bit(argv[i]);

    QCoreApplication::setApplicationName(QStringLiteral("taos-bar"));
    QCoreApplication::setApplicationVersion(QStringLiteral(TAOS_BAR_VERSION));

    QCommandLineParser p;
    p.setApplicationDescription(QStringLiteral("taOS status bar"));
    p.addHelpOption();
    QCommandLineOption optVersion(QStringLiteral("version"), QStringLiteral("Print the version and exit."));
    QCommandLineOption optHeight(QStringLiteral("height"), QStringLiteral("Bar height in logical px (default 40)."), QStringLiteral("N"), QStringLiteral("40"));
    QCommandLineOption optVariant(QStringLiteral("variant"), QStringLiteral("Theme variant: dark or light (env TAOS_THEME_VARIANT, default dark)."), QStringLiteral("dark|light"));
    QCommandLineOption optLauncher(QStringLiteral("launcher-cmd"), QStringLiteral("Command run by the taOS button (default foot)."), QStringLiteral("cmd"), QStringLiteral("foot"));
    QCommandLineOption optOutput(QStringLiteral("output"), QStringLiteral("Only show the bar on this output."), QStringLiteral("name"));
    QCommandLineOption optTheme(QStringLiteral("theme"), QStringLiteral("Theme id (default taos-default)."), QStringLiteral("id"), QStringLiteral("taos-default"));
    QCommandLineOption optReduce(QStringLiteral("reduce-motion"), QStringLiteral("No animations (env TAOS_REDUCE_MOTION=1)."));
    QCommandLineOption optSeconds(QStringLiteral("seconds"), QStringLiteral("Show seconds in the clock."));
    QCommandLineOption opt12h(QStringLiteral("12h"), QStringLiteral("Use a 12 hour clock."));
    p.addOptions({optVersion, optHeight, optVariant, optLauncher, optOutput, optTheme, optReduce, optSeconds, opt12h});
    if (!p.parse(args)) {
        fprintf(stderr, "taos-bar: %s\n", qPrintable(p.errorText()));
        return 2;
    }
    if (p.isSet(optVersion)) {
        printf("taos-bar %s\n", TAOS_BAR_VERSION);
        return 0;
    }
    if (p.isSet(QStringLiteral("help"))) {
        printf("%s", qPrintable(p.helpText()));
        return 0;
    }
    bool okH = false;
    const int height = p.value(optHeight).toInt(&okH);
    if (!okH || height < 16 || height > 200) {
        fprintf(stderr, "taos-bar: --height must be a number between 16 and 200\n");
        return 2;
    }
    QString variant = p.isSet(optVariant) ? p.value(optVariant) : qEnvironmentVariable("TAOS_THEME_VARIANT", QStringLiteral("dark"));
    if (variant != QLatin1String("dark") && variant != QLatin1String("light")) {
        fprintf(stderr, "taos-bar: variant must be dark or light, got '%s'\n", qPrintable(variant));
        return 2;
    }
    const bool reduce = p.isSet(optReduce) || qEnvironmentVariable("TAOS_REDUCE_MOTION") == QLatin1String("1");

    // A bar is a layer surface on a Wayland compositor; nothing else is meaningful.
    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) qputenv("QT_QPA_PLATFORM", "wayland");
    if (qEnvironmentVariableIsEmpty("QT_WAYLAND_SHELL_INTEGRATION")) qputenv("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell");

    QGuiApplication app(argc, argv);
    app.setQuitOnLastWindowClosed(false);

    if (pipe(g_sigPipe) != 0) return 1;
    struct sigaction sa {};
    sa.sa_handler = onSignal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, nullptr);
    sigaction(SIGINT, &sa, nullptr);
    QSocketNotifier sigNotifier(g_sigPipe[0], QSocketNotifier::Read);
    QObject::connect(&sigNotifier, &QSocketNotifier::activated, &app, [&] {
        char c;
        const ssize_t r = read(g_sigPipe[0], &c, 1);
        (void)r;
        QCoreApplication::quit();
    });

    Theme theme;
    QStringList themeDirs;
    if (!qEnvironmentVariableIsEmpty("TAOS_THEME_DIR")) themeDirs << qEnvironmentVariable("TAOS_THEME_DIR");
    themeDirs << QStringLiteral("/usr/share/taos/themes");
    QString err;
    if (!theme.load(p.value(optTheme), variant, themeDirs,
                    QStringLiteral(":/taos/bar/resources/themes/taos-default/theme.json"), reduce, &err)) {
        fprintf(stderr, "taos-bar: theme error: %s\n", qPrintable(err));
        return 1;
    }

    Clock clock;
    clock.setShowSeconds(p.isSet(optSeconds));
    clock.setUse24Hour(!p.isSet(opt12h));
    // The system bus in the session; TAOS_BAR_DBUS_ADDRESS points both clients at another bus (tests, demos).
    QDBusConnection netBus = QDBusConnection::systemBus();
    if (!qEnvironmentVariableIsEmpty("TAOS_BAR_DBUS_ADDRESS"))
        netBus = QDBusConnection::connectToBus(qEnvironmentVariable("TAOS_BAR_DBUS_ADDRESS"), QStringLiteral("taos-bar-net"));
    NmClient nm(netBus);
    BluezClient bluez(netBus);
    MenuHost menuHost(&nm, &bluez);
    Network network;
    network.attachNm(&nm);
    nm.start();
    bluez.start();
    Volume volume;
    Battery battery;
    Launcher launcher;
    launcher.setCommand(p.value(optLauncher));

    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Theme", &theme);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Clock", &clock);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Network", &network);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Wifi", &nm);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Bluetooth", &bluez);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "MenuHost", &menuHost);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Volume", &volume);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Battery", &battery);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Launcher", &launcher);

    BarManager::Options o;
    o.height = height;
    o.onlyOutput = p.value(optOutput);
    o.qmlUrl = QStringLiteral("qrc:/taos/bar/qml/Bar.qml");
    o.menuQmlUrl = QStringLiteral("qrc:/taos/bar/qml/NetMenu.qml");
    o.host = &menuHost;
    BarManager mgr(o);

    fprintf(stderr, "taos-bar %s outputs=%s theme=%s/%s source=%s height=%d reduce-motion=%d\n", TAOS_BAR_VERSION,
            qPrintable(mgr.describeOutputs()), qPrintable(theme.id()), qPrintable(theme.variant()),
            qPrintable(theme.source()), height, int(theme.reduceMotion()));
    mgr.start();
    return app.exec();
}
