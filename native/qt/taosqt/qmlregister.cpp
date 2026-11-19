// Compiled only when Qt Qml is available (TAOSQT_WITH_QML).
#include <QtQml/qqml.h>

#include "taosqt/api.h"
#include "taosqt/client.h"
#include "taosqt/eventstream.h"
#include "taosqt/jsonmodel.h"
#include "taosqt/livequery.h"
#include "taosqt/qmlregister.h"
#include "taosqt/session.h"

namespace Taos {

void registerQmlTypes()
{
    const char *uri = "Taos";
    qmlRegisterUncreatableType<Client>(uri, 1, 0, "Client", QStringLiteral("owned by the Session"));
    qmlRegisterUncreatableType<Session>(uri, 1, 0, "Session", QStringLiteral("created by the app"));
    qmlRegisterUncreatableType<EventStream>(uri, 1, 0, "EventStream", QStringLiteral("created by C++"));
    qmlRegisterUncreatableType<LiveQuery>(uri, 1, 0, "LiveQuery", QStringLiteral("created by C++"));
    qmlRegisterUncreatableType<JsonListModel>(uri, 1, 0, "JsonListModel", QStringLiteral("created by C++"));
    // Named TaosError: a QML type called Error would shadow the JavaScript global.
    qmlRegisterUncreatableMetaObject(Error::staticMetaObject, uri, 1, 0, "TaosError", QStringLiteral("enum only"));
}

} // namespace Taos
