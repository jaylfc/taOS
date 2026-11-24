#include <LayerShellQt/Window>
#include <QCommandLineParser>
#include <QGuiApplication>
#include <QIcon>
#include <QQmlContext>
#include <QQmlEngine>
#include <QSocketNotifier>
#include <QtQml>
#include <csignal>
#include <cstdio>
#include <unistd.h>

#include "dockcli.h"
#include "dockipc.h"
#include "dockcontroller.h"
#include "dockmanager.h"
#include "iconprovider.h"
#include "theme.h"
#include "thememanager.h"

static int g_sigPipe[2] = {-1, -1};
static void onSignal(int) { const char c = 1; const ssize_t r = write(g_sigPipe[1], &c, 1); (void)r; }

int main(int argc, char **argv) {
    QStringList args;
    for (int i = 0; i < argc; ++i) args << QString::fromLocal8Bit(argv[i]);
    if (args.size() >= 2 && (args.at(1) == QLatin1String("--version"))) { printf("taos-dock %s\n", TAOS_DOCK_VERSION); return 0; }
    if (args.size() >= 2 && (args.at(1) == QLatin1String("--help") || args.at(1) == QLatin1String("-h"))) {
        printf("usage: taos-dock [run]\n       taos-dock get [key] | set <key> <value> | pin <id> | unpin <id> | list\n       taos-dock reveal | hide | toggle | focus   (to the running dock; focus is for Super+D)\n"
               "keys: position (bottom|left|right) attached autohide size (small|medium|large) magnify outputs (primary|all)\n"
               "The setting is ~/.config/taos/dock.json; a running dock follows changes at once.\n");
        return 0;
    }
    if (args.size() >= 2 && DockCli::isCommand(args.at(1))) {
        QCoreApplication cli(argc, argv);
        QString out, err;
        const int rc = DockCli::run(args.mid(1), QString(), &out, &err);
        fputs(qPrintable(out), stdout);
        fputs(qPrintable(err), stderr);
        return rc;
    }
    if (args.size() >= 2 && DockIpc::isCommand(args.at(1))) {   // reveal | hide | toggle | focus
        QCoreApplication cli(argc, argv);
        QString e;
        if (!DockIpc::send(args.at(1), &e)) { fprintf(stderr, "taos-dock: %s\n", qPrintable(e)); return 1; }
        return 0;
    }
    if (args.size() >= 2 && args.at(1) != QLatin1String("run")) {
        fprintf(stderr, "taos-dock: unknown command '%s' (try --help)\n", qPrintable(args.at(1)));
        return 2;
    }

    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) qputenv("QT_QPA_PLATFORM", "wayland");
    if (qEnvironmentVariableIsEmpty("QT_WAYLAND_SHELL_INTEGRATION")) qputenv("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell");
    QGuiApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("taos-dock"));
    app.setQuitOnLastWindowClosed(false);
    if (pipe(g_sigPipe) != 0) return 1;
    struct sigaction sa {};
    sa.sa_handler = onSignal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, nullptr);
    sigaction(SIGINT, &sa, nullptr);
    QSocketNotifier sigNotifier(g_sigPipe[0], QSocketNotifier::Read);
    QObject::connect(&sigNotifier, &QSocketNotifier::activated, &app, [&] { char c; const ssize_t r = read(g_sigPipe[0], &c, 1); (void)r; QCoreApplication::quit(); });

    const bool reduce = qEnvironmentVariable("TAOS_REDUCE_MOTION") == QLatin1String("1");
    Theme theme;
    ThemeManager::Options to;
    to.pinnedTheme = qEnvironmentVariable("TAOS_THEME");
    to.pinnedVariant = qEnvironmentVariable("TAOS_THEME_VARIANT");
    to.reduceMotion = reduce;
    ThemeManager themes(&theme, to);
    QString err;
    if (!themes.start(&err)) { fprintf(stderr, "taos-dock: theme error: %s\n", qPrintable(err)); return 1; }

    DockController::Options o;
    o.engine = qEnvironmentVariable("TAOS_DOCK_ENGINE", QStringLiteral("auto"));
    DockController dock(o);

    qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "Theme", &theme);
    qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "Dock", &dock);
    qmlRegisterSingletonInstance("Taos.Dock", 1, 0, "DockSettings", dock.settings());
    QQmlEngine engine;
    engine.addImageProvider(QStringLiteral("dockicon"), new DockIconProvider);
    DockManager mgr(&dock, &engine);
    DockIpc ipc;
    if (!ipc.listen()) fprintf(stderr, "taos-dock: cannot listen on %s: Super+D will not work\n", qPrintable(DockIpc::socketPath()));
    QObject::connect(&ipc, &DockIpc::command, &dock, &DockController::handleCommand);
    dock.start();
    mgr.start();
    fprintf(stderr, "taos-dock %s engine=%s config=%s\n", TAOS_DOCK_VERSION, qPrintable(dock.engine()->name()), qPrintable(dock.settings()->path()));
    return app.exec();
}
