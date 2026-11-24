// SPDX-License-Identifier: AGPL-3.0-or-later
#include "dbusutil.h"

#include <QDBusMetaType>
#include <QDBusPendingReply>
#include <QDBusVariant>

namespace dbusutil {

void registerTypes() {
    static bool done = false;
    if (done) return;
    done = true;
    qDBusRegisterMetaType<DBusSettings>();
    qDBusRegisterMetaType<DBusManagedObjects>();
    qDBusRegisterMetaType<QList<QDBusObjectPath>>();
}

QVariant unwrap(const QVariant &v) {
    if (v.metaType() == QMetaType::fromType<QDBusVariant>()) return unwrap(v.value<QDBusVariant>().variant());
    return v;
}

QVariantMap toMap(const QVariant &v) {
    const QVariant u = unwrap(v);
    if (u.metaType() == QMetaType::fromType<QDBusArgument>()) return qdbus_cast<QVariantMap>(u.value<QDBusArgument>());
    if (u.metaType() == QMetaType::fromType<QVariantMap>()) return u.toMap();
    return {};
}

QStringList toPaths(const QVariant &v) {
    const QVariant u = unwrap(v);
    QStringList out;
    if (u.metaType() == QMetaType::fromType<QDBusArgument>()) {
        const QList<QDBusObjectPath> l = qdbus_cast<QList<QDBusObjectPath>>(u.value<QDBusArgument>());
        for (const auto &p : l) out << p.path();
    } else if (u.metaType() == QMetaType::fromType<QList<QDBusObjectPath>>()) {
        for (const auto &p : u.value<QList<QDBusObjectPath>>()) out << p.path();
    } else if (u.metaType() == QMetaType::fromType<QDBusObjectPath>()) {
        out << u.value<QDBusObjectPath>().path();
    }
    return out;
}

QByteArray toBytes(const QVariant &v) {
    const QVariant u = unwrap(v);
    if (u.metaType() == QMetaType::fromType<QDBusArgument>()) return qdbus_cast<QByteArray>(u.value<QDBusArgument>());
    return u.toByteArray();
}

DBusSettings toSettings(const QVariant &v) {
    const QVariant u = unwrap(v);
    if (u.metaType() == QMetaType::fromType<QDBusArgument>()) return qdbus_cast<DBusSettings>(u.value<QDBusArgument>());
    return u.value<DBusSettings>();
}

DBusManagedObjects toManaged(const QVariant &v) {
    const QVariant u = unwrap(v);
    if (u.metaType() == QMetaType::fromType<QDBusArgument>())
        return qdbus_cast<DBusManagedObjects>(u.value<QDBusArgument>());
    return u.value<DBusManagedObjects>();
}

bool isError(const QDBusMessage &reply) { return reply.type() == QDBusMessage::ErrorMessage; }

void call(const QDBusConnection &bus, const QString &service, const QString &path, const QString &iface,
          const QString &method, const QVariantList &args, QObject *ctx,
          std::function<void(const QDBusMessage &)> done, int timeoutMs) {
    QDBusMessage m = QDBusMessage::createMethodCall(service, path, iface, method);
    m.setArguments(args);
    QDBusConnection b = bus;
    auto *w = new QDBusPendingCallWatcher(b.asyncCall(m, timeoutMs), ctx);
    QObject::connect(w, &QDBusPendingCallWatcher::finished, ctx, [w, done = std::move(done)] {
        const QDBusMessage reply = w->reply();
        w->deleteLater();
        if (done) done(reply);
    });
}

void getAll(const QDBusConnection &bus, const QString &service, const QString &path, const QString &iface,
            QObject *ctx, std::function<void(const QVariantMap &, bool)> done) {
    call(bus, service, path, QStringLiteral("org.freedesktop.DBus.Properties"), QStringLiteral("GetAll"), {iface}, ctx,
         [done = std::move(done)](const QDBusMessage &r) {
             if (isError(r) || r.arguments().isEmpty()) {
                 done({}, false);
                 return;
             }
             done(toMap(r.arguments().at(0)), true);
         });
}

void setProperty(const QDBusConnection &bus, const QString &service, const QString &path, const QString &iface,
                 const QString &name, const QVariant &value, QObject *ctx,
                 std::function<void(const QDBusMessage &)> done) {
    call(bus, service, path, QStringLiteral("org.freedesktop.DBus.Properties"), QStringLiteral("Set"),
         {iface, name, QVariant::fromValue(QDBusVariant(value))}, ctx, std::move(done));
}

}  // namespace dbusutil
