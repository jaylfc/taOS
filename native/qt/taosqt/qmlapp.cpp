#include "taosqt/qmlapp.h"

#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickWindow>
#include <QTimer>
#include <QWindow>
#include <QtQml/qqml.h>

#include "taosqt/appcontext.h"
#include "taosqt/desktopsession.h"
#include "taosqt/qmlregister.h"
#ifdef TAOSQT_WITH_STYLE
#include "glass.h"
#include "theme.h"
#endif

using namespace Qt::StringLiterals;

namespace Taos {

int runQmlApp(int argc, char *argv[], const AppInfo &info, const QUrl &mainQml)
{
    QGuiApplication app(argc, argv);
    App::configure(info);
    SingleInstance single(info.appId);
    if (!single.acquire(app.arguments().mid(1)))
        return 0; // the running instance was activated instead

    ClientOptions options;
    if (qEnvironmentVariableIsSet("TAOS_URL"))
        options.baseUrl = QUrl(qEnvironmentVariable("TAOS_URL"));
    Session session(options);
    session.setTokenSource(new DesktopSessionTokenSource);
    setDefaultClient(session.client());

    registerQmlTypes();
    AppContext context(&session, info.appId);
#ifdef TAOSQT_WITH_STYLE
    // The same theme the shell components use: TAOS_THEME_DIR, then the system
    // themes, then the built-in taos-default. TAOS_THEME_VARIANT=light|dark.
    Theme theme;
    QStringList themeDirs;
    if (!qEnvironmentVariableIsEmpty("TAOS_THEME_DIR"))
        themeDirs << qEnvironmentVariable("TAOS_THEME_DIR");
    themeDirs << u"/usr/share/taos/themes"_s;
    const QString variant = qEnvironmentVariable("TAOS_THEME_VARIANT", u"dark"_s);
    QString themeError;
    Style style(&theme);
    if (theme.load(qEnvironmentVariable("TAOS_THEME", u"taos-default"_s), variant, themeDirs,
                   u":/taos/qt/themes/taos-default/theme.json"_s, qEnvironmentVariableIsSet("TAOS_REDUCE_MOTION"),
                   &themeError))
        context.setStyle(&style);
    else
        qWarning("taosqt: theme not loaded (%s); using built-in colours", qUtf8Printable(themeError));
#endif
    QQmlApplicationEngine engine;
    context.setEngine(&engine);
    qmlRegisterSingletonInstance("Taos", 1, 0, "Taos", &context);
    engine.addImportPath(u"qrc:/taos/qml"_s);
    engine.load(mainQml);
    if (engine.rootObjects().isEmpty())
        return 1;
    QObject::connect(&single, &SingleInstance::activationRequested, &engine, [&engine] {
        if (auto *w = qobject_cast<QWindow *>(engine.rootObjects().value(0))) {
            w->show();
            w->raise();
            w->requestActivate();
        }
    });
    session.start();
    // Screenshots without a compositor (CI, offscreen): TAOS_SCREENSHOT=<png>
    // [TAOS_SCREENSHOT_DELAY_MS=2000] grabs the first window, then quits.
    if (qEnvironmentVariableIsSet("TAOS_SCREENSHOT")) {
        const int delay = qEnvironmentVariableIntValue("TAOS_SCREENSHOT_DELAY_MS") > 0
            ? qEnvironmentVariableIntValue("TAOS_SCREENSHOT_DELAY_MS") : 2000;
        QTimer::singleShot(delay, &app, [&engine, &app] {
            auto *w = qobject_cast<QQuickWindow *>(engine.rootObjects().value(0));
            const bool ok = w && w->grabWindow().save(qEnvironmentVariable("TAOS_SCREENSHOT"));
            app.exit(ok ? 0 : 3);
        });
    }
    return app.exec();
}

} // namespace Taos
