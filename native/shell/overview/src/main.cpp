#include <QCommandLineParser>
#include <QElapsedTimer>
#include <QGuiApplication>
#include <QQmlContext>
#include <QQmlEngine>
#include <QSocketNotifier>
#include <QtQml>
#include <csignal>
#include <cstdio>
#include <unistd.h>

#include "appinfo.h"
#include "capture.h"
#include "control.h"
#include "controller.h"
#include "glass.h"
#include "iconprovider.h"
#include "overlayhost.h"
#include "theme.h"
#include "thumbnails.h"

static int g_sigPipe[2] = {-1, -1};
static void onSignal(int) {
    const char c = 1;
    const ssize_t r = write(g_sigPipe[1], &c, 1);
    (void)r;
}

static const char *kUsage =
    "usage: taos-overview <command>\n"
    "  daemon [options]          run the overlay (the session starts this one)\n"
    "  switcher [--reverse]      show the Alt+Tab switcher, or move its selection when shown\n"
    "  overview                  show the workspace overview, or close it when shown\n"
    "  hide                      close without changing focus\n"
    "  commit                    focus the selected window and close\n"
    "  next | prev | left | right | up | down   move the selection\n"
    "  status                    print the daemon's state as JSON\n"
    "  --sticky                  (switcher) stay until Return or Escape instead of until Alt is released\n"
    "daemon options: [--variant dark|light] [--theme id] [--reduce-motion] [--no-capture]\n";

// The client path: no Qt objects at all, so `taos-overview switcher` bound to a key costs a socket write.
static int runClient(int argc, char **argv) {
    QStringList words;
    for (int i = 1; i < argc; ++i) words << QString::fromLocal8Bit(argv[i]);
    QString reply;
    const int rc = Control::send(Control::socketPath(), words, &reply);
    if (rc == 2) {
        fprintf(stderr, "taos-overview: the daemon is not running (no answer on %s)\n", qPrintable(Control::socketPath()));
        return 1;
    }
    if (reply.startsWith(QLatin1String("ok "))) printf("%s\n", qPrintable(reply.mid(3)));
    else if (rc != 0) fprintf(stderr, "taos-overview: %s\n", qPrintable(reply));
    return rc;
}

int main(int argc, char **argv) {
    QElapsedTimer startup;
    startup.start();
    const QString first = argc > 1 ? QString::fromLocal8Bit(argv[1]) : QString();
    if (first == QLatin1String("--help") || first == QLatin1String("-h")) { fputs(kUsage, stdout); return 0; }
    if (first == QLatin1String("--version")) { printf("taos-overview %s\n", TAOS_OVERVIEW_VERSION); return 0; }
    if (!first.isEmpty() && first != QLatin1String("daemon") && !first.startsWith(QLatin1String("--"))) return runClient(argc, argv);

    QStringList args;
    for (int i = 0; i < argc; ++i) args << QString::fromLocal8Bit(argv[i]);
    if (args.size() > 1 && args[1] == QLatin1String("daemon")) args.removeAt(1);
    QCommandLineParser p;
    QCommandLineOption optVariant(QStringLiteral("variant"), QStringLiteral("dark or light"), QStringLiteral("dark|light"));
    QCommandLineOption optTheme(QStringLiteral("theme"), QStringLiteral("Theme id"), QStringLiteral("id"), QStringLiteral("taos-default"));
    QCommandLineOption optReduce(QStringLiteral("reduce-motion"), QStringLiteral("No animations."));
    QCommandLineOption optNoCap(QStringLiteral("no-capture"), QStringLiteral("No live thumbnails (icons only)."));
    p.addOptions({ optVariant, optTheme, optReduce, optNoCap });
    if (!p.parse(args)) { fprintf(stderr, "taos-overview: %s\n%s", qPrintable(p.errorText()), kUsage); return 2; }
    QString variant = p.isSet(optVariant) ? p.value(optVariant) : qEnvironmentVariable("TAOS_THEME_VARIANT", QStringLiteral("dark"));
    if (variant != QLatin1String("dark") && variant != QLatin1String("light")) {
        fprintf(stderr, "taos-overview: variant must be dark or light\n");
        return 2;
    }
    const bool reduce = p.isSet(optReduce) || qEnvironmentVariable("TAOS_REDUCE_MOTION") == QLatin1String("1");

    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) qputenv("QT_QPA_PLATFORM", "wayland");
    if (qEnvironmentVariableIsEmpty("QT_WAYLAND_SHELL_INTEGRATION")) qputenv("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell");
    QCoreApplication::setApplicationName(QStringLiteral("taos-overview"));
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
                    QStringLiteral(":/taos/overview/resources/themes/taos-default/theme.json"), reduce, &err)) {
        fprintf(stderr, "taos-overview: theme error: %s\n", qPrintable(err));
        return 1;
    }
    Style style(&theme);

    AppInfo apps;
    apps.startScan();
    ThumbStore store;
    ThumbnailItem::setStore(&store);
    Controller controller(&apps);
    controller.setThumbStore(&store);

    CaptureWorker capture;
    bool capOk = false;
    if (!p.isSet(optNoCap)) {
        QString cerr;
        capOk = capture.start(&cerr);
        if (!capOk) fprintf(stderr, "taos-overview: live thumbnails off: %s\n", qPrintable(cerr));
        else controller.setCapture(&capture);
    }

    QQmlEngine::setObjectOwnership(&controller, QQmlEngine::CppOwnership);
    qmlRegisterSingletonInstance("Taos.Overview", 1, 0, "Style", &style);
    qmlRegisterSingletonInstance("Taos.Overview", 1, 0, "Overview", &controller);
    qmlRegisterType<ThumbnailItem>("Taos.Overview", 1, 0, "ThumbnailItem");

    QQmlEngine engine;
    engine.addImageProvider(QStringLiteral("icon"), new IconProvider(&apps));
    OverlayHost host(&engine, &controller);
    if (!host.load(QUrl(QStringLiteral("qrc:/taos/overview/qml/Overlay.qml")), &err)) {
        fprintf(stderr, "taos-overview: qml: %s\n", qPrintable(err));
        return 1;
    }
    if (!controller.connectSway(&err)) {
        fprintf(stderr, "taos-overview: %s\n", qPrintable(err));
        return 1;
    }
    ControlServer server;
    server.setHandler([&](const QStringList &w) { return controller.handleCommand(w); });
    if (!server.listen(Control::socketPath(), &err)) {
        fprintf(stderr, "taos-overview: %s\n", qPrintable(err));
        return 1;
    }
    fprintf(stderr, "taos-overview %s theme=%s/%s capture=%d reduce-motion=%d ready in %lld ms\n", TAOS_OVERVIEW_VERSION,
            qPrintable(theme.id()), qPrintable(theme.variant()), int(capOk), int(theme.reduceMotion()),
            static_cast<long long>(startup.elapsed()));
    return app.exec();
}
