// SPDX-License-Identifier: AGPL-3.0-or-later
#include "service.h"

#include <QDBusConnectionInterface>
#include <QDBusError>
#include <QDBusMessage>
#include <QDateTime>
#include <QCoreApplication>
#include <QDir>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
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

void Service::writeStatus(const QString &state, const QString &reason) const
{
    QJsonObject o{{u"state"_s, state}, {u"accepted"_s, m_accepted}, {u"filtered_by_drop_list"_s, m_filtered},
                  {u"transient_skipped"_s, m_transient}, {u"pending"_s, m_buf->count()},
                  {u"quarantined"_s, m_fwd->quarantined()}};
    if (m_fwd->unwarned() > 0) { // refused records kept in rejected/ that nobody has been told about yet
        o[u"rejected_unwarned"_s] = m_fwd->unwarned();
        o[u"warning"_s] = u"a refused notification is in rejected/ and its warning could not be recorded: %1"_s.arg(m_fwd->warningError());
    }
    if (!reason.isEmpty())
        o[u"reason"_s] = reason;
    o[u"pid"_s] = qint64(QCoreApplication::applicationPid());
    const QString path = QDir(m_buf->dir()).filePath(u"status"_s);
    if (state != u"serving"_s) {
        // An idle second instance must not overwrite the status of a live serving one.
        QFile old(path);
        if (old.open(QIODevice::ReadOnly)) {
            const QJsonObject prev = QJsonDocument::fromJson(old.readAll()).object();
            const qint64 pid = prev.value(u"pid"_s).toInteger();
            if (prev.value(u"state"_s).toString() == u"serving"_s && pid != QCoreApplication::applicationPid()
                && QFile::exists(u"/proc/%1"_s.arg(pid)))
                return;
        }
    }
    QSaveFile f(path);
    if (f.open(QIODevice::WriteOnly)) {
        f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
        f.commit();
    }
}

quint32 Service::notify(const QString &appName, quint32 replacesId, const QString &appIcon, const QString &summary,
                        const QString &body, const QStringList &actions, const QVariantMap &hints, int expireTimeout,
                        QString *errorOut)
{
    quint64 seq = 0;
    QString err;
    if (!m_buf->nextSeq(&seq, &err)) {
        if (errorOut) *errorOut = err;
        return 0; // never carry on with a number that could repeat
    }
    const QString sender = calledFromDBus() ? message().service() : QString();
    // A replace only applies to a notification this same app connection raised in this run;
    // anything else is a new notification.
    FdInfo replaced;
    bool replacing = false;
    if (replacesId > 0 && m_fd.contains(replacesId) && m_fd.value(replacesId).sender == sender) {
        replaced = m_fd.value(replacesId);
        replacing = true;
    }
    const quint32 fdId = replacing ? replacesId : quint32(seq);
    const QString clientId = m_buf->clientId(seq);
    const Notification n = fromNotify(appName, fdId, clientId, replacing ? replaced.firstClientId : QString(), appIcon,
                                      summary, body, actions, hints, expireTimeout, QDateTime::currentSecsSinceEpoch());
    if (m_drop.matches(appKey(n))) {
        ++m_filtered;
        QTextStream(stderr) << "taos-notifyd: dropped a notification from " << appKey(n) << " (drop list)\n";
        writeStatus(u"serving"_s);
        return fdId;
    }
    if (n.transient && !n.critical()) {
        // Transient means "show, do not keep"; there is no popup to show it, and the controller does not store
        // it. It is counted (never its content) so it does not vanish without trace. Critical ones are kept.
        ++m_transient;
        QTextStream(stderr) << "taos-notifyd: skipped a transient notification from " << appKey(n) << " (counted)\n";
        writeStatus(u"serving"_s);
        return fdId;
    }
    QJsonObject rec = n.toJson();
    if (n.transient)
        rec[u"transient"_s] = false; // a critical transient is kept like any other: the controller stores it
    const auto r = m_buf->append(seq, n.critical(), rec);
    if (!r.ok) {
        if (errorOut) *errorOut = r.error;
        return 0;
    }
    if (r.dropped > 0)
        QTextStream(stderr) << "taos-notifyd: buffer full, dropped " << r.dropped << " oldest notifications after a warning record\n";
    if (!replacing) {
        m_fd.insert(fdId, {sender, clientId});
        m_fdOrder.append(fdId);
        while (m_fdOrder.size() > 4096)
            m_fd.remove(m_fdOrder.takeFirst());
    }
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
