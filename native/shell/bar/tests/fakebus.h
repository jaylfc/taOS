#pragma once
// Test support: a private session bus and a tiny scriptable D-Bus object tree
// that stands in for NetworkManager and BlueZ.
#include <QDBusConnection>
#include <QDBusError>
#include <QDBusMessage>
#include <QDBusObjectPath>
#include <QDBusVariant>
#include <QDBusVirtualObject>
#include <QProcess>
#include <QtTest>
#include <functional>

#include "dbusutil.h"

inline QVariant opath(const QString &p) { return QVariant::fromValue(QDBusObjectPath(p)); }

class PrivateBus {
public:
    bool start() {
        m_proc.start(QStringLiteral(TAOS_DBUS_DAEMON),
                     {QStringLiteral("--session"), QStringLiteral("--nofork"), QStringLiteral("--print-address=1")});
        if (!m_proc.waitForStarted(5000) || !m_proc.waitForReadyRead(5000)) return false;
        m_address = QString::fromUtf8(m_proc.readLine()).trimmed();
        return !m_address.isEmpty();
    }
    ~PrivateBus() {  // only the daemon this object started
        m_proc.terminate();
        m_proc.waitForFinished(3000);
    }
    QDBusConnection connect(const QString &name) const { return QDBusConnection::connectToBus(m_address, name); }
    QString address() const { return m_address; }
private:
    QProcess m_proc;
    QString m_address;
};

class FakeTree : public QDBusVirtualObject {
public:
    using Handler = std::function<void(const QDBusMessage &)>;
    explicit FakeTree(const QDBusConnection &bus, QObject *parent = nullptr) : QDBusVirtualObject(parent), m_bus(bus) {
        dbusutil::registerTypes();
        m_bus.registerVirtualObject(QStringLiteral("/"), this, QDBusConnection::SubPath);
    }
    QDBusConnection bus() const { return m_bus; }
    QMap<QString, DBusSettings> objs;  // path -> interface -> properties
    QHash<QString, Handler> methods;   // "interface.Method"
    QStringList calls;                 // "interface.Method path" in order
    QHash<QString, QVariantList> lastArgs;  // "interface.Method" -> arguments

    QString introspect(const QString &) const override { return QStringLiteral("<node/>"); }

    bool handleMessage(const QDBusMessage &m, const QDBusConnection &) override {
        if (m.type() != QDBusMessage::MethodCallMessage) return false;
        const QString iface = m.interface(), name = m.member();
        if (iface == QLatin1String("org.freedesktop.DBus.Introspectable")) {
            send(m.createReply(QStringLiteral("<node/>")));
            return true;
        }
        if (iface == QLatin1String("org.freedesktop.DBus.Properties")) {
            const QVariantList a = m.arguments();
            const QVariantMap props = objs.value(m.path()).value(a.value(0).toString());
            if (name == QLatin1String("GetAll")) {
                send(m.createReply(QVariant(props)));
            } else if (name == QLatin1String("Get")) {
                send(m.createReply(QVariant::fromValue(QDBusVariant(props.value(a.value(1).toString())))));
            } else if (name == QLatin1String("Set")) {
                calls << QStringLiteral("Properties.Set %1.%2").arg(a.value(0).toString(), a.value(1).toString());
                lastArgs[QStringLiteral("Set.") + a.value(1).toString()] = {dbusutil::unwrap(a.value(2))};
                const auto h = methods.value(QStringLiteral("Properties.Set"));
                if (h) h(m);
                else { setProp(m.path(), a.value(0).toString(), a.value(1).toString(), dbusutil::unwrap(a.value(2))); send(m.createReply()); }
            }
            return true;
        }
        if (iface == QLatin1String("org.freedesktop.DBus.ObjectManager") && name == QLatin1String("GetManagedObjects")) {
            DBusManagedObjects all;
            for (auto it = objs.cbegin(); it != objs.cend(); ++it) all.insert(QDBusObjectPath(it.key()), it.value());
            send(m.createReply(QVariant::fromValue(all)));
            return true;
        }
        const QString key = iface + QLatin1Char('.') + name;
        calls << key + QLatin1Char(' ') + m.path();
        lastArgs[key] = m.arguments();
        const Handler h = methods.value(key);
        if (!h) {
            send(m.createErrorReply(QStringLiteral("org.freedesktop.DBus.Error.UnknownMethod"), key));
            return true;
        }
        h(m);
        return true;
    }

    void send(const QDBusMessage &m) { m_bus.send(m); }
    void reply(const QDBusMessage &m, const QVariantList &args = {}) {
        QDBusMessage r = m.createReply();
        r.setArguments(args);
        send(r);
    }
    void error(const QDBusMessage &m, const QString &name, const QString &text = QStringLiteral("x")) {
        send(m.createErrorReply(name, text));
    }
    void emitSignal(const QString &path, const QString &iface, const QString &name, const QVariantList &args) {
        QDBusMessage s = QDBusMessage::createSignal(path, iface, name);
        s.setArguments(args);
        send(s);
    }
    void setProp(const QString &path, const QString &iface, const QString &name, const QVariant &v) {
        objs[path][iface][name] = v;
        emitSignal(path, QStringLiteral("org.freedesktop.DBus.Properties"), QStringLiteral("PropertiesChanged"),
                   {iface, QVariant(QVariantMap{{name, v}}), QVariant(QStringList())});
    }
    bool called(const QString &prefix) const {
        for (const QString &c : calls) if (c.startsWith(prefix)) return true;
        return false;
    }
    int count(const QString &prefix) const {
        int n = 0;
        for (const QString &c : calls) if (c.startsWith(prefix)) ++n;
        return n;
    }
private:
    QDBusConnection m_bus;
};
