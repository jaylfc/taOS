// SPDX-License-Identifier: AGPL-3.0-or-later
// taos-settings: the system control panel. Its own main() (not the generated one) because it needs the theme
// engine as an object, deep links, and a single-instance socket under $XDG_RUNTIME_DIR/taos/.
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQuickWindow>
#include <QTimer>
#include <QtQml/qqml.h>

#include "taosqt/app.h"
#include "taosqt/appcontext.h"
#include "taosqt/client.h"
#include "taosqt/desktopsession.h"
#include "taosqt/qmlregister.h"

#include "context.h"
#include "storeengine.h"
#include "instance.h"

using namespace Qt::StringLiterals;

int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    Taos::App::configure({u"my.taos.settings"_s, u"Settings"_s, u"0.1"_s});
    const QStringList args = app.arguments().mid(1);
    if (args.contains(u"--help"_s) || args.contains(u"-h"_s)) {
        printf("usage: taos-settings [--section <id>] [--setting <id>] [--search <text>]\n"
               "sections: appearance display dock desktop sound network about\n");
        return 0;
    }

    TaosSettings::InstanceGuard single;
    if (!single.acquire(args))
        return 0; // the running window was told where to go

    TaosSettings::Options o;
    o.theme.pinnedTheme = qEnvironmentVariable("TAOS_THEME");
    o.theme.pinnedVariant = qEnvironmentVariable("TAOS_THEME_VARIANT");
    o.theme.reduceMotion = qEnvironmentVariableIsSet("TAOS_REDUCE_MOTION");
    o.pulseServer = qEnvironmentVariable("TAOS_BAR_PULSE_SERVER");
    o.dbusAddress = qEnvironmentVariable("TAOS_BAR_DBUS_ADDRESS");
    TaosSettings::SettingsContext settings(o);
    settings.nav()->applyArguments(args);

    Taos::ClientOptions copts;
    if (qEnvironmentVariableIsSet("TAOS_URL"))
        copts.baseUrl = QUrl(qEnvironmentVariable("TAOS_URL"));
    Taos::Session session(copts);
    session.setTokenSource(new Taos::DesktopSessionTokenSource);
    Taos::setDefaultClient(session.client());
    Taos::registerQmlTypes();
    Taos::Store::registerStoreQmlTypes();   // Settings > Apps
    Taos::AppContext context(&session, u"my.taos.settings"_s);
    context.setStyle(settings.style());

    QQmlApplicationEngine engine;
    context.setEngine(&engine);
    qmlRegisterSingletonInstance("Taos", 1, 0, "Taos", &context);
    qmlRegisterSingletonInstance("Taos.Settings", 1, 0, "Settings", &settings);
    engine.addImportPath(u"qrc:/taos/qml"_s);
    engine.load(QUrl(u"qrc:/my.taos.settings/Main.qml"_s));
    if (engine.rootObjects().isEmpty())
        return 1;
    auto *window = qobject_cast<QQuickWindow *>(engine.rootObjects().value(0));
    QObject::connect(&single, &TaosSettings::InstanceGuard::activated, &settings, [&](const QStringList &a) {
        settings.nav()->applyArguments(a);
        if (window) {
            window->show();
            window->raise();
            window->requestActivate();
        }
    });
    session.start();

    // Renders without a compositor: TAOS_SCREENSHOT=<png> [TAOS_SCREENSHOT_DELAY_MS] [TAOS_SCREENSHOT_SIZE=WxH].
    if (qEnvironmentVariableIsSet("TAOS_SCREENSHOT") && window) {
        const int delay = qEnvironmentVariableIntValue("TAOS_SCREENSHOT_DELAY_MS") > 0 ? qEnvironmentVariableIntValue("TAOS_SCREENSHOT_DELAY_MS") : 2000;
        const QStringList sz = qEnvironmentVariable("TAOS_SCREENSHOT_SIZE").split(u'x');
        if (sz.size() == 2)
            window->resize(sz[0].toInt(), sz[1].toInt());
        QTimer::singleShot(delay, &app, [&app, window] {
            app.exit(window->grabWindow().save(qEnvironmentVariable("TAOS_SCREENSHOT")) ? 0 : 3);
        });
    }
    return app.exec();
}
