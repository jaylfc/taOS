// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// org.freedesktop.Notifications on the session bus: the server side of the Desktop
// Notifications Specification 1.2. Each accepted Notify is written to the write-ahead buffer
// before the call returns, then handed to the Forwarder.
#include <QDBusAbstractAdaptor>
#include <QDBusConnection>
#include <QDBusContext>
#include <QObject>
#include <QSet>
#include <QStringList>
#include <QVariantMap>

#include "buffer.h"
#include "notification.h"
#include "forwarder.h"

namespace Taos::Notifyd {

/// Notifications from these apps are dropped at the door (a lower-case desktop entry or app name,
/// ".desktop" optional). Empty by default; the phone package fills it with the kiosk browser (test N9).
class DropList
{
public:
    DropList() = default;
    explicit DropList(const QString &commaSeparated);
    bool matches(const QString &appKey) const { return m_keys.contains(appKey); }
    bool isEmpty() const { return m_keys.isEmpty(); }

private:
    QSet<QString> m_keys;
};

class Service : public QObject, public QDBusContext
{
    Q_OBJECT
public:
    Service(WriteAheadBuffer *buffer, Forwarder *fwd, const DropList &drop, QObject *parent = nullptr);
    /// Registers the object and the well-known name WITHOUT queueing and WITHOUT allowing replacement:
    /// another notification server keeps the name. Returns false (with `error`) when it is taken.
    bool start(const QDBusConnection &bus, QString *error);
    int filtered() const { return m_filtered; }
    int accepted() const { return m_accepted; }

    // Called by the adaptor.
    quint32 notify(const QString &appName, quint32 replacesId, const QString &appIcon, const QString &summary,
                   const QString &body, const QStringList &actions, const QVariantMap &hints, int expireTimeout,
                   QString *errorOut);
    void close(quint32 id);
    /// Answer the call being handled with a D-Bus error (the app sees it).
    void failCall(const QString &message);

Q_SIGNALS:
    void notificationClosed(quint32 id, quint32 reason);

private:
    WriteAheadBuffer *m_buf;
    Forwarder *m_fwd;
    DropList m_drop;
    int m_filtered = 0, m_accepted = 0, m_transient = 0;
};

class NotificationsAdaptor : public QDBusAbstractAdaptor
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.freedesktop.Notifications")
public:
    explicit NotificationsAdaptor(Service *svc);

public Q_SLOTS:
    uint Notify(const QString &app_name, uint replaces_id, const QString &app_icon, const QString &summary,
                const QString &body, const QStringList &actions, const QVariantMap &hints, int expire_timeout);
    void CloseNotification(uint id);
    QStringList GetCapabilities();
    QString GetServerInformation(QString &vendor, QString &version, QString &spec_version);

Q_SIGNALS:
    void NotificationClosed(uint id, uint reason);
    void ActionInvoked(uint id, const QString &action_key);

private:
    Service *m_svc;
};

} // namespace Taos::Notifyd
