// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QCommandLineParser>
#include <QElapsedTimer>
#include <QFontDatabase>
#include <QGuiApplication>
#include <QQmlContext>
#include <QQmlEngine>
#include <QSocketNotifier>
#include <QtQml>
#include <csignal>
#include <cstdio>
#include <unistd.h>

#include "actionmenu.h"
#include "appmenu.h"
#include "controller.h"
#include "glass.h"
#include "host.h"
#include "iconprovider.h"
#include "ipc.h"
#include "setup.h"
#include "theme.h"
#include "thememanager.h"

using namespace taos;

static int g_sigPipe[2] = {-1, -1};
static void onSignal(int) {
    const char c = 1;
    const ssize_t r = write(g_sigPipe[1], &c, 1);
    (void)r;
}

static const char *kUsage =
    "usage: taos-launcher <command>\n"
    "  daemon [options]   run the launcher, resident and hidden (the session supervisor starts this)\n"
    "  toggle             show the launcher, or hide it when shown\n"
    "  show | hide        show or hide it\n"
    "  status             print the daemon's state as JSON\n"
    "  menu | pinned | recent | pin ID | unpin ID | move-pin ID N | actions [PATH] | run ACTION [confirm] | show-actions\n"
    "                     the app menu and the system actions, as JSON (agents); `taos-search menu` does the same without a daemon\n"
    "daemon options: [--variant dark|light|auto] [--theme id] [--style centered|anchored|ring|text|phone] [--reduce-motion]\n"
    "  --style forces a launcher style whatever the theme says (a phone home screen: --style text).\n";

static int runClient(const QStringList &words) {
    const QString path = launcher::socketPath();
    QString reply;
    int rc = launcher::sendCommand(path, words, &reply);
    if (rc == 2) { fprintf(stderr, "taos-launcher: the daemon is not running (no answer on %s); it is a supervised session component, start it with `taos-launcher daemon` when there is no supervisor\n", qPrintable(path)); return 1; }
    if (reply.startsWith(QLatin1String("ok "))) printf("%s\n", qPrintable(reply.mid(3)));
    else if (rc != 0) fprintf(stderr, "taos-launcher: %s\n", qPrintable(reply));
    return rc;
}

int main(int argc, char **argv) {
    QElapsedTimer startup;
    startup.start();
    const QString first = argc > 1 ? QString::fromLocal8Bit(argv[1]) : QStringLiteral("daemon");
    if (first == QLatin1String("--help") || first == QLatin1String("-h")) { fputs(kUsage, stdout); return 0; }
    if (first == QLatin1String("--version")) { printf("taos-launcher %s\n", TAOS_LAUNCHER_VERSION); return 0; }
    if (first != QLatin1String("daemon") && !first.startsWith(QLatin1String("--"))) {
        QStringList w;
        for (int i = 1; i < argc; ++i) w << QString::fromLocal8Bit(argv[i]);
        return runClient(w);
    }

    QStringList args;
    for (int i = 0; i < argc; ++i) args << QString::fromLocal8Bit(argv[i]);
    if (args.size() > 1 && args[1] == QLatin1String("daemon")) args.removeAt(1);
    QCommandLineParser p;
    QCommandLineOption optVariant(QStringLiteral("variant"), QStringLiteral("dark, light or auto; overrides appearance."), QStringLiteral("dark|light|auto"));
    QCommandLineOption optTheme(QStringLiteral("theme"), QStringLiteral("Theme id; overrides appearance."), QStringLiteral("id"));
    QCommandLineOption optStyle(QStringLiteral("style"), QStringLiteral("Force a launcher style: centered, anchored, ring, text or phone."), QStringLiteral("style"));
    QCommandLineOption optReduce(QStringLiteral("reduce-motion"), QStringLiteral("No animations."));
    p.addOptions({ optVariant, optTheme, optStyle, optReduce });
    if (!p.parse(args)) { fprintf(stderr, "taos-launcher: %s\n%s", qPrintable(p.errorText()), kUsage); return 2; }
    QString variant = p.isSet(optVariant) ? p.value(optVariant) : qEnvironmentVariable("TAOS_THEME_VARIANT");
    if (!variant.isEmpty() && !AppearanceFile::validMode(variant)) { fprintf(stderr, "taos-launcher: variant must be dark, light or auto\n"); return 2; }
    QString styleOverride = p.isSet(optStyle) ? p.value(optStyle) : qEnvironmentVariable("TAOS_LAUNCHER_STYLE");
    if (!styleOverride.isEmpty() && !QStringList{ "centered", "anchored", "ring", "text", "phone" }.contains(styleOverride)) {
        fprintf(stderr, "taos-launcher: style must be centered, anchored, ring, text or phone\n");
        return 2;
    }
    const bool reduce = p.isSet(optReduce) || qEnvironmentVariable("TAOS_REDUCE_MOTION") == QLatin1String("1");

    // single instance, decided before any window work
    QString err;
    launcher::Server server;
    QStringList ownEnv;   // what we set for ourselves; programs we launch must not inherit it (see runner.cpp)
    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) { qputenv("QT_QPA_PLATFORM", "wayland"); ownEnv << QStringLiteral("QT_QPA_PLATFORM"); }
    if (qEnvironmentVariableIsEmpty("QT_WAYLAND_SHELL_INTEGRATION")) { qputenv("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell"); ownEnv << QStringLiteral("QT_WAYLAND_SHELL_INTEGRATION"); }
    qputenv("TAOS_LAUNCHER_OWN_ENV", ownEnv.join(QLatin1Char(',')).toUtf8());
    QCoreApplication::setApplicationName(QStringLiteral("taos-launcher"));
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
    ThemeManager::Options o;
    o.pinnedTheme = p.isSet(optTheme) ? p.value(optTheme) : qEnvironmentVariable("TAOS_THEME");
    o.pinnedVariant = variant;
    o.reduceMotion = reduce;
    ThemeManager themes(&theme, o);
    if (!themes.start(&err)) { fprintf(stderr, "taos-launcher: theme error: %s\n", qPrintable(err)); return 1; }
    Style style(&theme);

    search::SearchEngine engine;
    const search::Installed installed = search::installDefaultProviders(&engine);
    launcher::Controller controller(&engine, installed.frecency);
    controller.setConfig(installed.config);
    controller.setStyleOverride(styleOverride);
    controller.setMenu(std::make_shared<search::AppMenu>(installed.apps, installed.frecency), std::make_shared<search::ActionMenu>());
    controller.setSender(std::make_shared<search::UnavailableSender>());
    controller.setLaunchDelay(theme.reduceMotion() ? 0 : 170);
    QQmlEngine::setObjectOwnership(&controller, QQmlEngine::CppOwnership);
    qmlRegisterSingletonInstance("Taos.Launcher", 1, 0, "Style", &style);
    qmlRegisterSingletonInstance("Taos.Launcher", 1, 0, "Launcher", &controller);

    QQmlEngine qml;
    qml.addImageProvider(QStringLiteral("icon"), new launcher::IconProvider());
    launcher::Host host(&qml, &controller);
    if (!host.load(QUrl(QStringLiteral("qrc:/taos/launcher/qml/Launcher.qml")), &err)) {
        fprintf(stderr, "taos-launcher: qml: %s\n", qPrintable(err));
        return 1;
    }
    engine.prepare();   // first scan of the desktop entries while nobody is waiting
    server.setHandler([&](const QStringList &w) { return controller.handleCommand(w); });
    if (!server.listen(launcher::socketPath(), &err)) { fprintf(stderr, "taos-launcher: %s\n", qPrintable(err)); return 1; }
    fprintf(stderr, "taos-launcher %s theme=%s/%s reduce-motion=%d ready in %lld ms\n", TAOS_LAUNCHER_VERSION, qPrintable(theme.id()),
            qPrintable(theme.variant()), int(theme.reduceMotion()), static_cast<long long>(startup.elapsed()));
    return app.exec();
}
