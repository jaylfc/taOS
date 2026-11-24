// SPDX-License-Identifier: AGPL-3.0-or-later
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
#include "thememanager.h"
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
    // The same theme the shell components use: the person's appearance.json (taos-theme sets it),
    // live. TAOS_THEME and TAOS_THEME_VARIANT=dark|light|auto override it (screenshots, tests).
    Theme theme;
    ThemeManager::Options themeOpts;
    themeOpts.pinnedTheme = qEnvironmentVariable("TAOS_THEME");
    themeOpts.pinnedVariant = qEnvironmentVariable("TAOS_THEME_VARIANT");
    themeOpts.reduceMotion = qEnvironmentVariableIsSet("TAOS_REDUCE_MOTION");
    ThemeManager themes(&theme, themeOpts);
    QString themeError;
    Style style(&theme);
    if (themes.start(&themeError))
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
    QObject::connect(&single, &SingleInstance::activationRequested, &engine, [&engine, &context](const QStringList &args) {
        context.announceActivation(args);
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
        // TAOS_SCREENSHOT_SIZE=WxH resizes the window first (default: the app's own size).
        const QStringList sz = qEnvironmentVariable("TAOS_SCREENSHOT_SIZE").split(QLatin1Char('x'));
        if (sz.size() == 2)
            if (auto *w = qobject_cast<QQuickWindow *>(engine.rootObjects().value(0)))
                w->resize(sz[0].toInt(), sz[1].toInt());
        QTimer::singleShot(delay, &app, [&engine, &app] {
            auto *w = qobject_cast<QQuickWindow *>(engine.rootObjects().value(0));
            const bool ok = w && w->grabWindow().save(qEnvironmentVariable("TAOS_SCREENSHOT"));
            app.exit(ok ? 0 : 3);
        });
    }
    return app.exec();
}

} // namespace Taos
