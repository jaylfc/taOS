// SPDX-License-Identifier: AGPL-3.0-or-later
#include "service.h"

#include <QDBusConnectionInterface>
#include <QDBusError>
#include <QDBusMessage>
#include <QDateTime>
#include <QTextStream>

using namespace Qt::StringLiterals;

namespace Taos::Notifyd {

static const QString kName = u"org.freedesktop.Notifications"_s;
static const QString kPath = u"/org/freedesktop/Notifications"_s;

DropList::DropList(const QString &csv)
{
    for (QString k : csv.split(u',', Qt::SkipEmptyParts)) {
        k = k.trimmed().toLower();
        if (k.endsWith(u".desktop"_s))
            k.chop(8);
        if (!k.isEmpty())
            m_keys.insert(k);
    }
}

Service::Service(WriteAheadBuffer *buffer, Forwarder *fwd, const DropList &drop, QObject *parent)
    : QObject(parent), m_buf(buffer), m_fwd(fwd), m_drop(drop)
{
    new NotificationsAdaptor(this);
}

bool Service::start(const QDBusConnection &bus, QString *error)
{
    QDBusConnection c = bus;
    if (!c.isConnected()) {
        if (error) *error = u"no session bus"_s;
        return false;
    }
    if (!c.registerObject(kPath, this, QDBusConnection::ExportAdaptors)) {
        if (error) *error = u"cannot register %1"_s.arg(kPath);
        return false;
    }
    const auto reply = c.interface()->registerService(kName, QDBusConnectionInterface::DontQueueService,
                                                      QDBusConnectionInterface::DontAllowReplacement);
    if (!reply.isValid() || reply.value() != QDBusConnectionInterface::ServiceRegistered) {
        c.unregisterObject(kPath);
        if (error) *error = u"%1 is owned by another notification server; not taking it over"_s.arg(kName);
        return false;
    }
    return true;
}

quint32 Service::notify(const QString &appName, quint32 replacesId, const QString &appIcon, const QString &summary,
                        const QString &body, const QStringList &actions, const QVariantMap &hints, int expireTimeout,
                        QString *errorOut)
{
    const quint64 seq = m_buf->nextSeq();
    const quint32 fdId = replacesId > 0 ? replacesId : quint32(seq);
    const Notification n = fromNotify(appName, fdId, seq, replacesId, appIcon, summary, body, actions, hints,
                                      expireTimeout, QDateTime::currentSecsSinceEpoch());
    if (m_drop.matches(appKey(n))) {
        ++m_filtered;
        QTextStream(stderr) << "taos-notifyd: dropped a notification from " << appKey(n) << " (drop list)\n";
        return fdId;
    }
    if (n.transient) {
        ++m_transient; // shown-not-stored by definition; there is no popup yet, so nothing to keep
        return fdId;
    }
    const auto r = m_buf->append(seq, n.critical(), n.toJson());
    if (!r.ok) {
        if (errorOut) *errorOut = r.error;
        return 0;
    }
    if (r.dropped > 0)
        QTextStream(stderr) << "taos-notifyd: buffer full, dropped " << r.dropped << " oldest notifications after a warning record\n";
    ++m_accepted;
    m_fwd->kick();
    return fdId;
}

void Service::failCall(const QString &message)
{
    sendErrorReply(QDBusError::Failed, message);
}

void Service::close(quint32 id)
{
    Q_EMIT notificationClosed(id, 3); // closed by a call to CloseNotification
}

NotificationsAdaptor::NotificationsAdaptor(Service *svc) : QDBusAbstractAdaptor(svc), m_svc(svc)
{
    connect(svc, &Service::notificationClosed, this, &NotificationsAdaptor::NotificationClosed);
}

uint NotificationsAdaptor::Notify(const QString &app_name, uint replaces_id, const QString &app_icon,
                                  const QString &summary, const QString &body, const QStringList &actions,
                                  const QVariantMap &hints, int expire_timeout)
{
    QString err;
    const uint id = m_svc->notify(app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout, &err);
    if (id == 0 && !err.isEmpty())
        m_svc->failCall(err); // the app sees the failure: nothing is lost silently
    return id;
}

void NotificationsAdaptor::CloseNotification(uint id) { m_svc->close(id); }

QStringList NotificationsAdaptor::GetCapabilities()
{
    // Only what is real today: no popup (so no actions, icons, persistence or markup claims).
    return {u"body"_s};
}

QString NotificationsAdaptor::GetServerInformation(QString &vendor, QString &version, QString &spec_version)
{
    vendor = u"taOS"_s;
    version = QStringLiteral(TAOS_NOTIFYD_VERSION);
    spec_version = u"1.2"_s;
    return u"taos-notifyd"_s;
}

} // namespace Taos::Notifyd
