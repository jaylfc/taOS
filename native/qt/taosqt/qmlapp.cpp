#include "taosqt/qmlapp.h"

#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QWindow>
#include <QtQml/qqml.h>

#include "taosqt/appcontext.h"
#include "taosqt/desktopsession.h"
#include "taosqt/qmlregister.h"

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
    return app.exec();
}

} // namespace Taos
