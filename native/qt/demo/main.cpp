// taos-native-demo: sign in to a controller and list its notifications live.
//   TAOS_URL=http://127.0.0.1:6969 TAOS_USER=jay TAOS_PASSWORD=... taos-native-demo
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickStyle>
#include <QWindow>

#include "taosqt/app.h"
#include "taosqt/livequery.h"
#include "taosqt/qmlregister.h"
#include "taosqt/session.h"

using namespace Qt::StringLiterals;

int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    QQuickStyle::setStyle(u"Basic"_s);
    Taos::App::configure({QStringLiteral(TAOS_APP_ID), QStringLiteral(TAOS_APP_NAME), u"0.1"_s});
    Taos::SingleInstance single(QStringLiteral(TAOS_APP_ID));
    if (!single.acquire(app.arguments().mid(1)))
        return 0; // another instance was activated instead

    Taos::ClientOptions options;
    if (qEnvironmentVariableIsSet("TAOS_URL"))
        options.baseUrl = QUrl(qEnvironmentVariable("TAOS_URL"));
    Taos::Session session(options);
    Taos::LiveQuery notifications(session.client(), u"notifications.list"_s, {}, {u"notification.added"_s},
                                  {u"id"_s, u"title"_s, u"message"_s, u"level"_s, u"read"_s});
    QObject::connect(&session, &Taos::Session::signedIn, &notifications, &Taos::LiveQuery::start);

    Taos::registerQmlTypes();
    QQmlApplicationEngine engine;
    engine.addImportPath(u"qrc:/"_s);
    engine.rootContext()->setContextProperty(u"session"_s, &session);
    engine.rootContext()->setContextProperty(u"notifications"_s, &notifications);
    engine.load(QUrl(u"qrc:/demo/main.qml"_s));
    QObject::connect(&single, &Taos::SingleInstance::activationRequested, &engine, [&engine] {
        if (auto *w = qobject_cast<QWindow *>(engine.rootObjects().value(0)))
            w->raise(), w->requestActivate();
    });

    session.start(); // asks the token source: the password source raises signInRequired
    if (qEnvironmentVariableIsSet("TAOS_USER") && qEnvironmentVariableIsSet("TAOS_PASSWORD"))
        session.signIn(qEnvironmentVariable("TAOS_USER"), qEnvironmentVariable("TAOS_PASSWORD"));
    return app.exec();
}
