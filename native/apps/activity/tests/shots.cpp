// activity-shots: offscreen screenshots of the Activity app's main views, from
// the QML sources, with the same context the generated main() builds.
//   activity-shots <controller url> <user> <password> <qml dir> <out dir> <scene>...
// Scenes: desktop, desktop-tall, light, restart-confirm, restart-done,
// restart-error, phone, phone-processes, phone-activity, unavailable.
// `FAKE_ACT` environment variable "http://host:port" is only a convention for
// the caller; the scene may POST fake_activity.py control calls itself with
// `--act <json>` pairs before it (see the tests README in the parity doc).
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQuickWindow>
#include <QTimer>
#include <QEventLoop>
#include <QDir>
#include <QtQml/qqml.h>

#include "engine.h"
#include "glass.h"
#include "taosqt/appcontext.h"
#include "taosqt/qmlregister.h"
#include "taosqt/session.h"
#include "appearance.h"
#include "theme.h"
#include "thememanager.h"
#include "types.h"

using namespace Qt::StringLiterals;

static void wait(int ms)
{
    QEventLoop l;
    QTimer::singleShot(ms, &l, &QEventLoop::quit);
    l.exec();
}

int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    if (argc < 7)
        return 2;
    const QString url = QString::fromLocal8Bit(argv[1]), user = QString::fromLocal8Bit(argv[2]),
                  pass = QString::fromLocal8Bit(argv[3]), qmlDir = QString::fromLocal8Bit(argv[4]),
                  outDir = QString::fromLocal8Bit(argv[5]);
    QStringList scenes;
    for (int i = 6; i < argc; ++i)
        scenes << QString::fromLocal8Bit(argv[i]);

    Taos::ClientOptions o;
    o.baseUrl = QUrl(url);
    Taos::Session session(o);
    Taos::setDefaultClient(session.client());
    Taos::registerQmlTypes();
    Taos::Activity::registerTypes();
    Taos::AppContext context(&session, u"my.taos.dashboard"_s);
    Theme theme;
    // The person's appearance.json (TAOS_APPEARANCE in tests) unless TAOS_THEME / TAOS_THEME_VARIANT pin it.
    ThemeManager::Options themeOpts;
    themeOpts.pinnedTheme = qEnvironmentVariable("TAOS_THEME");
    themeOpts.pinnedVariant = qEnvironmentVariable("TAOS_THEME_VARIANT");
    themeOpts.reduceMotion = qEnvironmentVariableIsSet("TAOS_REDUCE_MOTION");
    ThemeManager themes(&theme, themeOpts);
    QString err;
    themes.start(&err);
    Style style(&theme);
    context.setStyle(&style);
    QQmlApplicationEngine engine;
    context.setEngine(&engine);
    qmlRegisterSingletonInstance("Taos", 1, 0, "Taos", &context);
    engine.addImportPath(u"qrc:/taos/qml"_s);
    engine.load(QUrl::fromLocalFile(QDir(qmlDir).filePath(u"Main.qml"_s)));
    if (engine.rootObjects().isEmpty())
        return 1;
    auto *win = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    session.signIn(user, pass);
    auto *eng = win->findChild<Taos::Activity::ActivityEngine *>(u"engine"_s);
    if (!eng)
        return 3;
    eng->setPollIntervalMs(250);   // fill the graphs quickly; the app itself polls every 2 s
    auto shot = [&](const QString &name) {
        wait(400);
        const bool ok = win->grabWindow().save(QDir(outDir).filePath(name + u".png"_s));
        qInfo("%s: %s", qUtf8Printable(name), ok ? "saved" : "FAILED");
    };
    auto call = [&](const char *fn) { QMetaObject::invokeMethod(win, fn); };
    for (const QString &scene : scenes) {
        const bool phone = scene.startsWith(u"phone"_s);
        win->resize(phone ? QSize(390, 844) : (scene == u"desktop-tall"_s ? QSize(1180, 2500) : QSize(1180, 860)));
        if (scene == u"desktop-1280"_s)
            win->resize(1280, 800);
        wait(300);
        if (scene == u"unavailable"_s) {
            wait(1500);
            shot(scene);
            continue;
        }
        // fill the graphs: ~45 samples at 250 ms
        for (int i = 0; i < 80 && eng->history().value(u"cpu"_s).toList().size() < 45; ++i)
            wait(250);
        wait(1200);
        if (scene == u"restart-confirm"_s || scene == u"restart-done"_s || scene == u"restart-error"_s) {
            eng->askRestart();
            if (scene != u"restart-confirm"_s) {
                eng->confirmRestart();
                for (int i = 0; i < 40 && eng->restartPhase() == int(Taos::Activity::ActivityEngine::RestartRunning); ++i)
                    wait(100);
            }
            wait(500);
        } else if (scene == u"phone-processes"_s) {
            call("showProcesses");
            wait(600);
        } else if (scene == u"phone-activity"_s) {
            call("showModelActivity");
            wait(800);
        }
        if (scene == u"live"_s) {
            // The six theme and mode combinations in ONE process: each is switched by rewriting
            // appearance.json (what taos-theme does) and the running window recolours itself.
            const QStringList themesList{u"graphite"_s, u"graphite-ice"_s, u"graphite-warm"_s};
            bool first = true;
            for (const QString &t : themesList)
                for (const QString &m : {u"dark"_s, u"light"_s}) {
                    if (!first) {
                        Appearance a;
                        a.theme = t;
                        a.mode = m;
                        AppearanceFile::write(AppearanceFile::userPath(), a);
                        for (int i = 0; i < 100 && !(themes.themeId() == t && themes.mode() == m); ++i)
                            wait(50);
                    }
                    first = false;
                    wait(600);  // the 150 ms cross-fade has finished
                    shot(u"activity-"_s + t + u"-"_s + m);
                }
            continue;
        }
        shot(scene);
        eng->dismissRestart();
        call("back");
    }
    return 0;
}
