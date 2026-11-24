#include <QCommandLineParser>
#include <QGuiApplication>
#include <QQmlEngine>
#include <QSocketNotifier>
#include <QtQml>
#include <csignal>
#include <cstdio>
#include <unistd.h>

#include "framecontroller.h"
#include "stripmanager.h"
#include "theme.h"
#include "thememanager.h"

static int g_sigPipe[2] = {-1, -1};
static void onSignal(int) { const char c = 1; const ssize_t r = write(g_sigPipe[1], &c, 1); (void)r; }

int main(int argc, char **argv)
{
    QStringList args;
    for (int i = 0; i < argc; ++i) args << QString::fromLocal8Bit(argv[i]);
    const QString cmd = args.value(1);
    if (cmd == QLatin1String("--version")) { printf("taos-frame %s\n", TAOS_FRAME_VERSION); return 0; }
    if (cmd == QLatin1String("--help") || cmd == QLatin1String("-h")) {
        printf("usage: taos-frame [run]\n       taos-frame menu | state | press <window-id> <close|minimise|full>   (to the running taos-frame)\n"
               "Draws the window buttons for windows that cannot draw their own (web apps). Which windows: ~/.config/taos/frame.json\n"
               "  {\"include\": [\"^chrome-\"], \"exclude\": [], \"frameAllForeign\": false}\n");
        return 0;
    }
    if (cmd == QLatin1String("menu") || cmd == QLatin1String("state") || cmd == QLatin1String("press")) {
        QCoreApplication cli(argc, argv);
        QByteArray reply;
        QString err;
        const int rc = FrameController::controlCommand(FrameController::defaultControlSocket(), args.mid(1).join(QLatin1Char(' ')), &reply, &err);
        if (rc && !err.isEmpty()) fprintf(stderr, "taos-frame: %s\n", qPrintable(err));
        else printf("%s\n", reply.constData());
        return rc;
    }
    if (!cmd.isEmpty() && cmd != QLatin1String("run")) {
        fprintf(stderr, "taos-frame: unknown command '%s' (try --help)\n", qPrintable(cmd));
        return 2;
    }

    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) qputenv("QT_QPA_PLATFORM", "wayland");
    if (qEnvironmentVariableIsEmpty("QT_WAYLAND_SHELL_INTEGRATION")) qputenv("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell");
    QGuiApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("taos-frame"));
    app.setQuitOnLastWindowClosed(false);
    if (pipe(g_sigPipe) != 0) return 1;
    struct sigaction sa {};
    sa.sa_handler = onSignal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, nullptr);
    sigaction(SIGINT, &sa, nullptr);
    QSocketNotifier sigNotifier(g_sigPipe[0], QSocketNotifier::Read);
    QObject::connect(&sigNotifier, &QSocketNotifier::activated, &app, [&] { char c; const ssize_t r = read(g_sigPipe[0], &c, 1); (void)r; QCoreApplication::quit(); });

    Theme theme;
    ThemeManager::Options to;
    to.pinnedTheme = qEnvironmentVariable("TAOS_THEME");
    to.pinnedVariant = qEnvironmentVariable("TAOS_THEME_VARIANT");
    to.reduceMotion = qEnvironmentVariable("TAOS_REDUCE_MOTION") == QLatin1String("1");
    ThemeManager themes(&theme, to);
    QString err;
    if (!themes.start(&err)) { fprintf(stderr, "taos-frame: theme error: %s\n", qPrintable(err)); return 1; }

    FrameController::Options fo;
    fo.manageSway = qEnvironmentVariable("TAOS_FRAME_MANAGE_SWAY", QStringLiteral("1")) != QLatin1String("0");
    FrameController ctl(&theme, fo);
    QObject::connect(&ctl, &FrameController::fatal, &app, [&](const QString &why) {
        fprintf(stderr, "taos-frame: %s\n", qPrintable(why));
        app.exit(1);
    });

    qmlRegisterSingletonInstance("Taos.Frame", 1, 0, "Theme", &theme);
    QQmlEngine engine;
    StripManager mgr(&ctl, &engine);
    if (!ctl.start(&err)) { fprintf(stderr, "taos-frame: %s\n", qPrintable(err)); return 1; }
    fprintf(stderr, "taos-frame %s following sway, config %s\n", TAOS_FRAME_VERSION, qPrintable(FrameConfigFile::userPath()));
    return app.exec();
}
