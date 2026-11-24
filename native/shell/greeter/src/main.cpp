#include <QCommandLineParser>
#include <QDir>
#include <QGuiApplication>
#include <QHostInfo>
#include <QProcess>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QScreen>
#include <QSocketNotifier>
#include <QTemporaryDir>
#include <QTimer>
#include <QtQml>
#include <csignal>
#include <cstdio>
#include <unistd.h>

#include "controller.h"
#include "fakegreetd.h"
#include "glass.h"
#include "thememanager.h"
#include "greetdclient.h"
#include "keystate.h"
#include "layoutplan.h"
#include "poweractions.h"
#include "theme.h"
#include "usermodel.h"
#include "wallpaperprovider.h"
#include "weathermodel.h"

static int g_sigPipe[2] = {-1, -1};
static void onSignal(int) {
    const char c = 1;
    const ssize_t r = write(g_sigPipe[1], &c, 1);
    (void)r;
}

int main(int argc, char **argv) {
    QStringList args;
    for (int i = 0; i < argc; ++i) args << QString::fromLocal8Bit(argv[i]);
    QCoreApplication::setApplicationName(QStringLiteral("taos-greeter"));
    QCoreApplication::setApplicationVersion(QStringLiteral(TAOS_GREETER_VERSION));

    QCommandLineParser p;
    p.setApplicationDescription(QStringLiteral("taOS login screen (greetd greeter)"));
    p.addHelpOption();
    QCommandLineOption optVersion(QStringLiteral("version"), QStringLiteral("Print the version and exit."));
    QCommandLineOption optDemo(QStringLiteral("demo"), QStringLiteral("Run against a built-in fake greetd (password \"demo\")."));
    QCommandLineOption optDemoUsers(QStringLiteral("demo-users"), QStringLiteral("Number of invented users in demo mode (1-6, default 1)."), QStringLiteral("N"), QStringLiteral("1"));
    QCommandLineOption optDemoOtp(QStringLiteral("demo-otp"), QStringLiteral("Demo: ask for a second, visible code (123456) after the password."));
    QCommandLineOption optSession(QStringLiteral("session-cmd"), QStringLiteral("Session command (default taos-desktop-session)."), QStringLiteral("cmd"), QStringLiteral("taos-desktop-session"));
    QCommandLineOption optVariant(QStringLiteral("variant"), QStringLiteral("dark, light or auto; overrides appearance (env TAOS_THEME_VARIANT)."), QStringLiteral("dark|light|auto"));
    QCommandLineOption optTheme(QStringLiteral("theme"), QStringLiteral("Theme id; overrides appearance."), QStringLiteral("id"));
    QCommandLineOption optReduce(QStringLiteral("reduce-motion"), QStringLiteral("No animations (env TAOS_REDUCE_MOTION=1)."));
    QCommandLineOption optSize(QStringLiteral("size"), QStringLiteral("Window size WxH instead of fullscreen (development)."), QStringLiteral("WxH"));
    QCommandLineOption opt12h(QStringLiteral("12h"), QStringLiteral("Show a 12 hour clock (default 24 hour)."));
    p.addOptions({opt12h, optVersion, optDemo, optDemoUsers, optDemoOtp, optSession, optVariant, optTheme, optReduce, optSize});
    if (!p.parse(args)) {
        fprintf(stderr, "taos-greeter: %s\n", qPrintable(p.errorText()));
        return 2;
    }
    if (p.isSet(optVersion)) {
        printf("taos-greeter %s\n", TAOS_GREETER_VERSION);
        return 0;
    }
    if (p.isSet(QStringLiteral("help"))) {
        printf("%s", qPrintable(p.helpText()));
        return 0;
    }
    QString variant = p.isSet(optVariant) ? p.value(optVariant) : qEnvironmentVariable("TAOS_THEME_VARIANT");
    if (!variant.isEmpty() && !AppearanceFile::validMode(variant)) {
        fprintf(stderr, "taos-greeter: variant must be dark, light or auto, got '%s'\n", qPrintable(variant));
        return 2;
    }
    bool okN = false;
    const int demoUsers = p.value(optDemoUsers).toInt(&okN);
    if (!okN || demoUsers < 1 || demoUsers > 6) {
        fprintf(stderr, "taos-greeter: --demo-users must be 1 to 6\n");
        return 2;
    }
    const QStringList sessionCmd = QProcess::splitCommand(p.value(optSession));
    if (sessionCmd.isEmpty()) {
        fprintf(stderr, "taos-greeter: empty --session-cmd\n");
        return 2;
    }
    const bool demo = p.isSet(optDemo);
    const bool reduce = p.isSet(optReduce) || qEnvironmentVariable("TAOS_REDUCE_MOTION") == QLatin1String("1");

    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) qputenv("QT_QPA_PLATFORM", "wayland");
    qputenv("QT_QUICK_CONTROLS_STYLE", "Basic");
    QGuiApplication app(argc, argv);

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
        QCoreApplication::exit(0);
    });

    Theme theme;
    ThemeManager::Options themeOpts;
    themeOpts.role = ThemeManager::Role::Greeter;
    themeOpts.pinnedTheme = p.isSet(optTheme) ? p.value(optTheme) : qEnvironmentVariable("TAOS_THEME");
    themeOpts.pinnedVariant = variant;
    themeOpts.reduceMotion = reduce;
    ThemeManager themes(&theme, themeOpts);
    {
        QString err;
        if (!themes.start(&err)) {
            fprintf(stderr, "taos-greeter: theme error: %s\n", qPrintable(err));
            return 1;
        }
    }
    Style style(&theme);

    // Which screen, which wallpaper.
    QSize pixels(1920, 1080);
    if (QScreen *scr = QGuiApplication::primaryScreen())
        pixels = QSizeF(scr->size() * scr->devicePixelRatio()).toSize();
    if (p.isSet(optSize)) {
        const QStringList wh = p.value(optSize).split(QLatin1Char('x'));
        if (wh.size() == 2 && wh[0].toInt() > 0 && wh[1].toInt() > 0) pixels = QSize(wh[0].toInt(), wh[1].toInt());
    }
    QString wallFile = WallpaperProvider::pick(WallpaperProvider::defaultDir(), theme.variant(), pixels);

    UserModel users;
    users.setAccent(theme.accent());
    QObject::connect(&themes, &ThemeManager::appearanceChanged, &app, [&] { users.setAccent(theme.target()[ThemeTokens::Accent]); });
    if (demo) {
        static const char *names[][2] = {{"alex", "Alex Rivera"}, {"sam", "Sam Okafor"}, {"jordan", "Jordan Lee"},
                                         {"riley", "Riley Chen"}, {"morgan", "Morgan Ivanova"}, {"quinn", "Quinn Dubois"}};
        QVector<UserEntry> v;
        for (int i = 0; i < demoUsers; ++i) {
            UserEntry e;
            e.username = QString::fromLatin1(names[i][0]);
            e.displayName = QString::fromUtf8(names[i][1]);
            e.uid = 1000 + i;
            v.append(e);
        }
        users.setUsers(v);
    } else {
        users.discover(qEnvironmentVariable("TAOS_GREETER_NO_DBUS") != QLatin1String("1"),
                       qEnvironmentVariable("TAOS_GREETER_PASSWD", QStringLiteral("/etc/passwd")));
    }

    WeatherModel weather;
    weather.refresh();
    PowerActions power;
    if (demo && qEnvironmentVariableIsEmpty("TAOS_GREETER_POWER_CMD")) power.setCommand(QStringLiteral("echo"));  // never a real systemctl in demo
    KeyState keys;
    app.installEventFilter(&keys);
    LayoutPlan plan;
    GreetdClient client;
    client.setSessionCommand(sessionCmd);
    GreeterController ctl(&client);
    ctl.setHostname(QHostInfo::localHostName());
    ctl.setWallpaper(wallFile.isEmpty() ? QUrl() : QUrl::fromLocalFile(wallFile));
    ctl.setDemo(demo);
    ctl.setClock24(!p.isSet(opt12h));
    // Selecting a person shows their theme (their world-readable copy, else the system default).
    QObject::connect(&ctl, &GreeterController::selectedUserChanged, &app, [&] {
        const int row = users.indexOfUser(ctl.selectedUser());
        themes.previewUser(row >= 0 ? int(users.uidAt(row)) : -1);
        wallFile = WallpaperProvider::pick(WallpaperProvider::defaultDir(), theme.variant(), pixels);
        ctl.setWallpaper(wallFile.isEmpty() ? QUrl() : QUrl::fromLocalFile(wallFile));
    });

    FakeGreetd fake;
    QTemporaryDir tmp;
    int exitCode = 0;
    QObject::connect(&ctl, &GreeterController::sessionStarted, &app, [&] {
        const QString line = QStringLiteral("session start: %1").arg(sessionCmd.join(QLatin1Char(' ')));
        if (demo) {
            printf("demo: %s (user %s)\n", qPrintable(line), qPrintable(ctl.selectedUser()));
            fflush(stdout);
        } else {
            fprintf(stderr, "taos-greeter: %s\n", qPrintable(line));
        }
        // Long enough for the QML curtain (240 ms) to reach black and be presented.
        QTimer::singleShot(theme.reduceMotion() ? 0 : 340, &app, [] { QCoreApplication::exit(0); });
    });
    QObject::connect(&ctl, &GreeterController::fatalError, &app, [&](const QString &m) {
        fprintf(stderr, "taos-greeter: fatal: %s\n", qPrintable(m));
        exitCode = 1;
        QTimer::singleShot(5000, &app, [&] { QCoreApplication::exit(1); });
    });

    qmlRegisterSingletonInstance("Taos.Greeter", 1, 0, "Style", &style);
    qmlRegisterSingletonInstance("Taos.Greeter", 1, 0, "Users", &users);
    qmlRegisterSingletonInstance("Taos.Greeter", 1, 0, "Weather", &weather);
    qmlRegisterSingletonInstance("Taos.Greeter", 1, 0, "Power", &power);
    qmlRegisterSingletonInstance("Taos.Greeter", 1, 0, "Caps", &keys);
    qmlRegisterSingletonInstance("Taos.Greeter", 1, 0, "Plan", &plan);
    qmlRegisterSingletonInstance("Taos.Greeter", 1, 0, "Greeter", &ctl);
    qmlRegisterUncreatableType<GreeterController>("Taos.Greeter", 1, 0, "GreeterPhase", QStringLiteral("enum only"));

    // Connect to greetd (or the fake one) once the UI is up.
    QString sock = qEnvironmentVariable("GREETD_SOCK");
    if (demo) {
        fake.setOtp(p.isSet(optDemoOtp));
        sock = tmp.path() + QStringLiteral("/greetd.sock");
        if (!fake.listen(sock)) {
            fprintf(stderr, "taos-greeter: cannot start the demo greetd\n");
            return 1;
        }
    }
    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("initialSize"), p.isSet(optSize) ? QVariant(pixels) : QVariant());
    QObject::connect(&engine, &QQmlApplicationEngine::objectCreationFailed, &app, [] { QCoreApplication::exit(3); }, Qt::QueuedConnection);
    engine.load(QUrl(QStringLiteral("qrc:/taos/greeter/qml/Main.qml")));
    if (engine.rootObjects().isEmpty()) return 3;

    fprintf(stderr, "taos-greeter %s theme=%s/%s wallpaper=%s screen=%dx%d users=%d reduce-motion=%d demo=%d\n",
            TAOS_GREETER_VERSION, qPrintable(theme.id()), qPrintable(theme.variant()),
            wallFile.isEmpty() ? "(none)" : qPrintable(wallFile), pixels.width(), pixels.height(), users.rowCount(),
            int(theme.reduceMotion()), int(demo));

    if (sock.isEmpty()) {
        ctl.reportFatal(QStringLiteral("greetd is not available: GREETD_SOCK is not set. This login screen must be started by greetd."));
    } else {
        QTimer::singleShot(0, &app, [&] {
            if (client.connectToGreetd(sock) && users.rowCount() == 1)
                ctl.select(users.usernameAt(0));
        });
    }
    const int rc = app.exec();
    return rc != 0 ? rc : exitCode;
}
