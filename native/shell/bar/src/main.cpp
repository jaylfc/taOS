// SPDX-License-Identifier: AGPL-3.0-or-later
#include <LayerShellQt/Window>
#include <QCommandLineParser>
#include <QGuiApplication>
#include <QQmlContext>
#include <QQmlEngine>
#include <QDBusConnection>
#include <QSocketNotifier>
#include <QScreen>
#include <QStandardPaths>
#include <QTimer>
#include <QtQml>
#include <csignal>
#include <cstdio>
#include <unistd.h>

#include "barmanager.h"
#include "audio.h"
#include "baripc.h"
#include "battery.h"
#include "pulsebackend.h"
#include "clock.h"
#include "displaycontroller.h"
#include "outputwatcher.h"
#include "bluezclient.h"
#include "launcher.h"
#include "menuhost.h"
#include "nmclient.h"
#include "network.h"
#include "theme.h"
#include "thememanager.h"

static int g_sigPipe[2] = {-1, -1};
static void onSignal(int) {
    const char c = 1;
    const ssize_t r = write(g_sigPipe[1], &c, 1);
    (void)r;
}

int main(int argc, char **argv) {
    QStringList args;
    for (int i = 0; i < argc; ++i) args << QString::fromLocal8Bit(argv[i]);

    // `taos-bar volume up|down|mute|mic-mute` (also `taos-bar mic-mute`): hand the key to the running bar and leave.
    if (args.size() >= 2 && (args.at(1) == QLatin1String("volume") || args.at(1) == QLatin1String("mic-mute"))) {
        QCoreApplication cli(argc, argv);
        QString msg;
        const int rc = BarIpc::send(args.mid(1), QString(), &msg);
        if (rc != 0) fprintf(stderr, "taos-bar: %s\n", qPrintable(msg));
        return rc;
    }

    QCoreApplication::setApplicationName(QStringLiteral("taos-bar"));
    QCoreApplication::setApplicationVersion(QStringLiteral(TAOS_BAR_VERSION));

    QCommandLineParser p;
    p.setApplicationDescription(QStringLiteral("taOS status bar"));
    p.addHelpOption();
    QCommandLineOption optVersion(QStringLiteral("version"), QStringLiteral("Print the version and exit."));
    QCommandLineOption optHeight(QStringLiteral("height"), QStringLiteral("Bar height in logical px (default 40)."), QStringLiteral("N"), QStringLiteral("40"));
    QCommandLineOption optVariant(QStringLiteral("variant"), QStringLiteral("Theme variant: dark, light or auto; overrides appearance.json (env TAOS_THEME_VARIANT)."), QStringLiteral("dark|light|auto"));
    QCommandLineOption optLauncher(QStringLiteral("launcher-cmd"), QStringLiteral("Command run by the taOS button (default foot)."), QStringLiteral("cmd"), QStringLiteral("foot"));
    QCommandLineOption optOutput(QStringLiteral("output"), QStringLiteral("Only show the bar on this output."), QStringLiteral("name"));
    QCommandLineOption optTheme(QStringLiteral("theme"), QStringLiteral("Theme id; overrides the person's choice in appearance.json."), QStringLiteral("id"));
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
    QString variant = p.isSet(optVariant) ? p.value(optVariant) : qEnvironmentVariable("TAOS_THEME_VARIANT");
    if (!variant.isEmpty() && !AppearanceFile::validMode(variant)) {
        fprintf(stderr, "taos-bar: variant must be dark, light or auto, got '%s'\n", qPrintable(variant));
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
    ThemeManager::Options themeOpts;
    themeOpts.pinnedTheme = p.isSet(optTheme) ? p.value(optTheme) : qEnvironmentVariable("TAOS_THEME");
    themeOpts.pinnedVariant = variant;
    themeOpts.reduceMotion = reduce;
    ThemeManager themes(&theme, themeOpts);
    {
        QString err;
        if (!themes.start(&err)) {
            fprintf(stderr, "taos-bar: theme error: %s\n", qPrintable(err));
            return 1;
        }
        for (const auto &r : themes.catalog().refused())
            fprintf(stderr, "taos-bar: theme '%s' refused: %s\n", qPrintable(r.id), qPrintable(r.message));
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
    // The sound server: TAOS_BAR_PULSE_SERVER (tests, demos) or the user's default.
    PulseBackend pulse(qEnvironmentVariable("TAOS_BAR_PULSE_SERVER"));
    Audio audio(&pulse);
    MenuHost menuHost(&nm, &bluez, &audio);
    BarIpc ipc(&audio);
    if (!ipc.listen()) fprintf(stderr, "taos-bar: cannot listen on %s: volume keys will not work\n", qPrintable(ipc.path()));
    Network network;
    network.attachNm(&nm);
    nm.start();
    bluez.start();
    Battery battery;
    Launcher launcher;
    launcher.setCommand(p.value(optLauncher));

    // Display: native mode and scale per output. Only meaningful under sway (SWAYSOCK); elsewhere the item hides itself.
    SwayMsg swayMsg;
    DisplayController display(&swayMsg, DisplayConfigFile());
    display.setSettleMs(400);
    OutputWatcher outputWatcher;
    const bool haveSway = !qEnvironmentVariableIsEmpty("SWAYSOCK");
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Display", &display);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Theme", &theme);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Themes", &themes);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Clock", &clock);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Network", &network);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Wifi", &nm);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Bluetooth", &bluez);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "MenuHost", &menuHost);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Audio", &audio);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Battery", &battery);
    qmlRegisterSingletonInstance("Taos.Bar", 1, 0, "Launcher", &launcher);

    BarManager::Options o;
    o.height = height;
    o.onlyOutput = p.value(optOutput);
    o.qmlUrl = QStringLiteral("qrc:/taos/bar/qml/Bar.qml");
    o.networkMenuUrl = QStringLiteral("qrc:/taos/bar/qml/NetMenu.qml");
    o.audioMenuUrl = QStringLiteral("qrc:/taos/bar/qml/AudioMenu.qml");
    o.displayMenuUrl = QStringLiteral("qrc:/taos/bar/qml/DisplayMenu.qml");
    o.osdUrl = QStringLiteral("qrc:/taos/bar/qml/Osd.qml");
    o.host = &menuHost;
    BarManager mgr(o);

    fprintf(stderr, "taos-bar %s outputs=%s theme=%s/%s source=%s height=%d reduce-motion=%d\n", TAOS_BAR_VERSION,
            qPrintable(mgr.describeOutputs()), qPrintable(theme.id()), qPrintable(theme.variant()),
            qPrintable(theme.source()), height, int(theme.reduceMotion()));
    QObject::connect(&audio, &Audio::flash, &mgr, &BarManager::showOsd);
    mgr.start();
    if (haveSway) {
        // Session start and every monitor event: native mode, saved or recommended scale. A mode change that
        // nobody has confirmed yet (first run of a monitor) goes through the Keep this setting? dialog.
        DisplayController::ApplyOptions applyOpts;
        applyOpts.unsaved = DisplayController::ApplyOptions::Trial;
        auto applyNow = [&] {
            if (display.trialActive()) return;  // our own change is waiting for an answer
            const auto rep = display.applyAll(applyOpts);
            for (const QString &e : rep.errors) fprintf(stderr, "taos-bar: display: %s\n", qPrintable(e));
            if (display.trialActive() && !menuHost.isOpen())
                if (QScreen *sc = QGuiApplication::primaryScreen())
                    emit menuHost.openRequested(QStringLiteral("display"), display.trialOutput().isEmpty() ? sc->name() : display.trialOutput(), sc->geometry().width() - 40);
        };
        QObject::connect(&outputWatcher, &OutputWatcher::outputsChanged, &app, applyNow);
        // The question must stay on screen until answered: if something closed the menu, bring it back.
        QObject::connect(&menuHost, &MenuHost::openChanged, &app, [&] {
            if (display.trialActive() && !menuHost.isOpen())
                QTimer::singleShot(50, &app, [&] {
                    if (display.trialActive() && !menuHost.isOpen())
                        if (QScreen *sc = QGuiApplication::primaryScreen())
                            emit menuHost.openRequested(QStringLiteral("display"), display.trialOutput(), sc->geometry().width() - 40);
                });
        });
        // After an answer, the next monitor that still waits gets its turn.
        QObject::connect(&display, &DisplayController::trialChanged, &app, [&] {
            if (!display.trialActive()) QTimer::singleShot(400, &app, applyNow);
        });
        display.refresh();
        applyNow();
        outputWatcher.start();
    }
    // Screenshots and demos: TAOS_BAR_OPEN_MENU=network|audio opens that menu once the bar is up (no pointer needed).
    const QString openMenu = qEnvironmentVariable("TAOS_BAR_OPEN_MENU");
    if (!openMenu.isEmpty())
        QTimer::singleShot(2000, &app, [&] {
            if (QScreen *s = QGuiApplication::primaryScreen())
                emit menuHost.openRequested(openMenu, s->name(), s->geometry().width() - 40);
        });
    return app.exec();
}
