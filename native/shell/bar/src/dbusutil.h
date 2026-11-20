#pragma once
#include <QDBusArgument>
#include <QDBusConnection>
#include <QDBusMessage>
#include <QDBusObjectPath>
#include <QDBusPendingCallWatcher>
#include <QMap>
#include <QObject>
#include <QVariantMap>
#include <functional>

// a{sa{sv}}: NetworkManager connection settings and BlueZ interface maps.
using DBusSettings = QMap<QString, QVariantMap>;
// a{oa{sa{sv}}}: org.freedesktop.DBus.ObjectManager.GetManagedObjects.
using DBusManagedObjects = QMap<QDBusObjectPath, DBusSettings>;
Q_DECLARE_METATYPE(DBusSettings)
Q_DECLARE_METATYPE(DBusManagedObjects)

// Small helpers shared by the NetworkManager and BlueZ clients. Everything is
// asynchronous: the bar's GUI thread never waits on a daemon.
namespace dbusutil {
void registerTypes();

// Unwrap a value that came out of a variant (QDBusVariant, QDBusArgument).
QVariant unwrap(const QVariant &v);
QVariantMap toMap(const QVariant &v);          // a{sv}
QStringList toPaths(const QVariant &v);        // ao
QByteArray toBytes(const QVariant &v);         // ay
DBusSettings toSettings(const QVariant &v);    // a{sa{sv}}
DBusManagedObjects toManaged(const QVariant &v);

// Calls a method and runs `done` with the reply (check isError(reply)) unless
// `ctx` was destroyed first.
void call(const QDBusConnection &bus, const QString &service, const QString &path, const QString &iface,
          const QString &method, const QVariantList &args, QObject *ctx,
          std::function<void(const QDBusMessage &)> done = {}, int timeoutMs = 25000);
bool isError(const QDBusMessage &reply);
// Properties.GetAll
void getAll(const QDBusConnection &bus, const QString &service, const QString &path, const QString &iface,
            QObject *ctx, std::function<void(const QVariantMap &, bool ok)> done);
void setProperty(const QDBusConnection &bus, const QString &service, const QString &path, const QString &iface,
                 const QString &name, const QVariant &value, QObject *ctx,
                 std::function<void(const QDBusMessage &)> done = {});
}  // namespace dbusutil
