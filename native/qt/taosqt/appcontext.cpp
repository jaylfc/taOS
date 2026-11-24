// SPDX-License-Identifier: AGPL-3.0-or-later
#include "taosqt/appcontext.h"

#include <QCoreApplication>
#include <QJSEngine>
#include <QJSValue>
#include <QJsonValue>

namespace Taos {

QStringList AppContext::arguments() const
{
    return QCoreApplication::arguments().mid(1);
}

AppContext::AppContext(Session *session, const QString &appId, QObject *parent)
    : QObject(parent), m_session(session), m_appId(appId)
{
}

void AppContext::deliver(Reply *r, const QJSValue &callback)
{
    r->then(this, [this, callback](const Result &res) {
        QJSValue cb = callback;
        if (!cb.isCallable() || !m_engine)
            return;
        cb.call({QJSValue(res.ok()), m_engine->toScriptValue(res.value.toVariant()), QJSValue(res.error.userMessage())});
    });
}

void AppContext::call(const QString &op, const QVariantMap &args, const QJSValue &callback)
{
    deliver(client()->call(op, QJsonObject::fromVariantMap(args)), callback);
}

void AppContext::http(const QString &method, const QString &path, const QVariantMap &query, const QVariant &body,
                      const QJSValue &callback)
{
    // Qt 6.12 hands a JS object over as a QVariant holding a QJSValue, which
    // QJsonValue::fromVariant turns into null (the request went out with no body).
    QVariant plain = body;
    if (plain.metaType() == QMetaType::fromType<QJSValue>())
        plain = qvariant_cast<QJSValue>(plain).toVariant();
    deliver(client()->http(method, path, QJsonObject::fromVariantMap(query), QJsonValue::fromVariant(plain)), callback);
}

} // namespace Taos
