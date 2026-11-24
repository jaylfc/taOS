// SPDX-License-Identifier: AGPL-3.0-or-later
#include "notificationfeed.h"

#include <QCoreApplication>
#include <QJsonDocument>
#include <QJsonValue>
#include <QTimer>
#include <QtQml/qqml.h>
#include <algorithm>

#include "taosqt/api.h"
#include "taosqt/jsonmodel.h"

using namespace Qt::StringLiterals;

namespace Taos::Apps {

namespace {
const QStringList kRoles = {u"id"_s, u"timestamp"_s, u"level"_s, u"title"_s, u"message"_s,
                            u"read"_s, u"source"_s, u"data"_s, u"pinned"_s};

QString appId(const QString &webId) { return u"my.taos."_s + webId; }

OpenTarget make(const QString &webId, const QVariantMap &props = {}) { return {appId(webId), props}; }
} // namespace

bool isPinnedSource(const QString &source)
{
    return source == u"auth_requests"_s || source == u"agent_scope_requests"_s;
}

OpenTarget routeFor(const QString &source, const QJsonObject &data)
{
    // A typed data.target wins over the source (targetToAction, then sourceToTarget).
    const QJsonObject target = data.value(u"target"_s).toObject();
    if (target.value(u"kind"_s).toString() == u"project_file"_s) {
        return make(u"projects"_s, {{u"projectId"_s, target.value(u"project_id"_s).toVariant().toString()},
                                    {u"tab"_s, u"files"_s},
                                    {u"filePath"_s, target.value(u"path"_s).toVariant().toString()}});
    }
    if (source == u"system.update"_s || source == u"system.lifecycle"_s)
        return make(u"settings"_s, {{u"section"_s, u"updates"_s}});
    if (source == u"disk_quota"_s)
        return make(u"settings"_s, {{u"section"_s, u"storage"_s}});
    static const QStringList cluster = {u"worker.join"_s, u"worker.online"_s, u"worker.leave"_s,
                                        u"backend.up"_s, u"backend.down"_s};
    if (cluster.contains(source))
        return make(u"cluster"_s);
    if (source == u"training.complete"_s || source == u"training.failed"_s || source == u"agent_framework"_s)
        return make(u"agents"_s);
    if (source == u"app.installed"_s || source == u"app.failed"_s)
        return make(u"store"_s);
    if (source == u"decisions"_s || source == u"auth_requests"_s)
        return make(u"decisions"_s);
    return {};
}

// ---- NotificationProxy ------------------------------------------------------

void NotificationProxy::refilter()
{
#if QT_VERSION >= QT_VERSION_CHECK(6, 9, 0)
    beginFilterChange();
    endFilterChange(QSortFilterProxyModel::Direction::Rows);
#else
    invalidateFilter();
#endif
}

NotificationProxy::NotificationProxy(QObject *parent) : QSortFilterProxyModel(parent)
{
    setDynamicSortFilter(true);
}

void NotificationProxy::setSourceModelChecked(QAbstractItemModel *m)
{
    setSourceModel(m);
    sort(0);
    const auto kick = [this] { scheduleRebuild(); };
    connect(m, &QAbstractItemModel::rowsInserted, this, kick);
    connect(m, &QAbstractItemModel::rowsRemoved, this, kick);
    connect(m, &QAbstractItemModel::rowsMoved, this, kick);
    connect(m, &QAbstractItemModel::modelReset, this, kick);
    connect(m, &QAbstractItemModel::dataChanged, this, kick);
    connect(m, &QAbstractItemModel::layoutChanged, this, kick);
    scheduleRebuild();
}

int NotificationProxy::roleOf(const char *name) const
{
    if (!sourceModel())
        return -1;
    const auto names = sourceModel()->roleNames();
    for (auto it = names.cbegin(); it != names.cend(); ++it)
        if (it.value() == name)
            return it.key();
    return -1;
}

qint64 NotificationProxy::idAt(int row) const
{
    return sourceModel()->data(sourceModel()->index(row, 0), roleOf("id")).toLongLong();
}

bool NotificationProxy::pinnedAt(int row) const
{
    const QModelIndex i = sourceModel()->index(row, 0);
    return sourceModel()->data(i, roleOf("pinned")).toBool()
        || isPinnedSource(sourceModel()->data(i, roleOf("source")).toString());
}

qint64 NotificationProxy::stampAt(int row) const
{
    return qint64(sourceModel()->data(sourceModel()->index(row, 0), roleOf("timestamp")).toDouble() * 1000.0);
}

bool NotificationProxy::isRead(const QModelIndex &si) const
{
    return sourceModel()->data(si, roleOf("read")).toBool()
        || m_readOverlay.contains(sourceModel()->data(si, roleOf("id")).toLongLong());
}

QVariant NotificationProxy::data(const QModelIndex &index, int role) const
{
    if (index.isValid() && sourceModel() && role == roleOf("read"))
        return isRead(mapToSource(index));
    return QSortFilterProxyModel::data(index, role);
}

bool NotificationProxy::lessThan(const QModelIndex &l, const QModelIndex &r) const
{
    const bool pl = pinnedAt(l.row()), pr = pinnedAt(r.row());
    if (pl != pr)
        return pl;
    const qint64 sl = stampAt(l.row()), sr = stampAt(r.row());
    if (sl != sr)
        return sl > sr;
    return idAt(l.row()) > idAt(r.row());
}

bool NotificationProxy::filterAcceptsRow(int row, const QModelIndex &) const
{
    if (!sourceModel())
        return false;
    const qint64 id = idAt(row);
    if (m_hiddenOverlay.contains(id) || !m_allowed.contains(id))
        return false;
    const QModelIndex si = sourceModel()->index(row, 0);
    if (!m_level.isEmpty()) {
        QString lvl = sourceModel()->data(si, roleOf("level")).toString();
        if (lvl != u"info"_s && lvl != u"success"_s && lvl != u"warning"_s && lvl != u"error"_s)
            lvl = u"info"_s; // unknown levels show as info, as the web app does
        if (lvl != m_level)
            return false;
    }
    if (!m_source.isEmpty() && sourceModel()->data(si, roleOf("source")).toString() != m_source)
        return false;
    if (m_unreadOnly && isRead(si))
        return false;
    return true;
}

void NotificationProxy::setFilters(const QString &level, const QString &source, bool unreadOnly)
{
    m_level = level;
    m_source = source;
    m_unreadOnly = unreadOnly;
    refilter();
}

void NotificationProxy::overlayRead(qint64 id, bool settled)
{
    m_readOverlay.insert(id);
    if (settled)
        m_settledRead.insert(id);
    scheduleRebuild();
}
void NotificationProxy::overlayHide(qint64 id, bool settled)
{
    m_hiddenOverlay.insert(id);
    if (settled)
        m_settledHidden.insert(id);
    scheduleRebuild();
}
void NotificationProxy::dropOverlay(qint64 id)
{
    m_readOverlay.remove(id);
    m_hiddenOverlay.remove(id);
    m_settledRead.remove(id);
    m_settledHidden.remove(id);
    scheduleRebuild();
}
void NotificationProxy::settle(qint64 id)
{
    if (m_readOverlay.contains(id))
        m_settledRead.insert(id);
    if (m_hiddenOverlay.contains(id))
        m_settledHidden.insert(id);
}
void NotificationProxy::pruneSettled()
{
    // Called after a refetch that followed the POST: the server's answer is now
    // in the model, so the optimistic overlay is no longer needed.
    for (qint64 id : std::as_const(m_settledRead))
        m_readOverlay.remove(id);
    for (qint64 id : std::as_const(m_settledHidden))
        m_hiddenOverlay.remove(id);
    m_settledRead.clear();
    m_settledHidden.clear();
    scheduleRebuild();
}

int NotificationProxy::unreadCount() const { return m_unread; }
int NotificationProxy::pinnedCount() const { return m_pinned; }
int NotificationProxy::totalCount() const { return m_total; }
QStringList NotificationProxy::sources() const { return m_sources; }

void NotificationProxy::scheduleRebuild()
{
    if (m_rebuildQueued)
        return;
    m_rebuildQueued = true;
    QTimer::singleShot(0, this, [this] {
        m_rebuildQueued = false;
        rebuild();
    });
}

void NotificationProxy::rebuild()
{
    QSet<qint64> allowed;
    QStringList sources;
    int unread = 0, pinned = 0, total = 0;
    if (sourceModel()) {
        struct E { qint64 id, stamp; };
        QList<E> rest;
        QSet<QString> src;
        const int n = sourceModel()->rowCount();
        for (int r = 0; r < n; ++r) {
            const qint64 id = idAt(r);
            if (m_hiddenOverlay.contains(id))
                continue;
            const QModelIndex si = sourceModel()->index(r, 0);
            const QString s = sourceModel()->data(si, roleOf("source")).toString();
            if (!s.isEmpty())
                src.insert(s);
            if (pinnedAt(r)) {
                allowed.insert(id);
                ++pinned;
            } else {
                rest.append({id, stampAt(r)});
            }
        }
        std::sort(rest.begin(), rest.end(), [](const E &a, const E &b) { return a.stamp != b.stamp ? a.stamp > b.stamp : a.id > b.id; });
        for (int i = 0; i < rest.size() && i < kRestCap; ++i)
            allowed.insert(rest.at(i).id);
        for (int r = 0; r < n; ++r) {
            if (!allowed.contains(idAt(r)))
                continue;
            ++total;
            if (!isRead(sourceModel()->index(r, 0)))
                ++unread;
        }
        sources = QStringList(src.cbegin(), src.cend());
        sources.sort();
    }
    m_allowed = allowed;
    m_sources = sources;
    m_unread = unread;
    m_pinned = pinned;
    m_total = total;
    refilter();
    if (rowCount() > 0)
        Q_EMIT dataChanged(index(0, 0), index(rowCount() - 1, 0), {roleOf("read")});
    Q_EMIT countsChanged();
}

// ---- NotificationFeed -------------------------------------------------------

NotificationFeed::NotificationFeed(QObject *parent) : QObject(parent), m_proxy(new NotificationProxy(this))
{
    connect(m_proxy, &NotificationProxy::countsChanged, this, &NotificationFeed::countsChanged);
}

NotificationFeed::NotificationFeed(Taos::Client *client, QObject *parent) : NotificationFeed(parent)
{
    m_client = client;
}

Taos::Client *NotificationFeed::client() const { return m_client ? m_client.data() : Taos::defaultClient(); }

void NotificationFeed::ensureQuery()
{
    if (m_query)
        return;
    Taos::Client *c = client();
    if (!c)
        return;
    m_query = new Taos::LiveQuery(c, u"notifications.list"_s, {}, {u"notification.added"_s}, kRoles, this);
    m_proxy->setSourceModelChecked(m_query->model());
    connect(m_query, &Taos::LiveQuery::loaded, this, [this] {
        m_loadedOnce = true;
        m_proxy->pruneSettled();
        Q_EMIT stateChanged();
    });
    connect(m_query, &Taos::LiveQuery::loadingChanged, this, &NotificationFeed::stateChanged);
    connect(m_query, &Taos::LiveQuery::errorChanged, this, &NotificationFeed::stateChanged);
    connect(m_query, &Taos::LiveQuery::liveChanged, this, &NotificationFeed::stateChanged);
}

void NotificationFeed::setRunning(bool on)
{
    if (m_running == on)
        return;
    m_running = on;
    if (on) {
        ensureQuery();
        if (m_query)
            m_query->start();
    } else if (m_query) {
        m_query->stop();
    }
    Q_EMIT runningChanged();
    Q_EMIT stateChanged();
}

void NotificationFeed::setLevelFilter(const QString &v)
{
    if (m_level == v)
        return;
    m_level = v;
    m_proxy->setFilters(m_level, m_source, m_unreadOnly);
    Q_EMIT filtersChanged();
}
void NotificationFeed::setSourceFilter(const QString &v)
{
    if (m_source == v)
        return;
    m_source = v;
    m_proxy->setFilters(m_level, m_source, m_unreadOnly);
    Q_EMIT filtersChanged();
}
void NotificationFeed::setUnreadOnly(bool v)
{
    if (m_unreadOnly == v)
        return;
    m_unreadOnly = v;
    m_proxy->setFilters(m_level, m_source, m_unreadOnly);
    Q_EMIT filtersChanged();
}

void NotificationFeed::refresh()
{
    if (m_query)
        m_query->refresh();
}

QVariantMap NotificationFeed::rowById(qint64 id) const
{
    if (!m_query)
        return {};
    auto *m = m_query->model();
    for (int r = 0; r < m->rowCount(); ++r)
        if (qint64(m->row(r).value(u"id"_s).toDouble()) == id)
            return m->get(r);
    return {};
}

QVariantMap NotificationFeed::targetFor(qint64 id) const
{
    const QVariantMap row = rowById(id);
    if (row.isEmpty())
        return {};
    const OpenTarget t = routeFor(row.value(u"source"_s).toString(),
                                  QJsonObject::fromVariantMap(row.value(u"data"_s).toMap()));
    return {{u"valid"_s, t.valid()}, {u"app"_s, t.app}, {u"props"_s, t.props}};
}

void NotificationFeed::afterAction(Taos::Reply *r, qint64 id, bool hide, const QString &failText)
{
    r->then(this, [this, id, hide, failText](const Taos::Result &res) {
        if (!res.ok()) {
            m_proxy->dropOverlay(id);
            Q_EMIT actionFailed(failText.isEmpty() ? res.error.userMessage() : failText + u" "_s + res.error.userMessage());
            return;
        }
        if (hide && res.value.toObject().value(u"outcome"_s).toString() == u"kept_pending_request"_s) {
            // The request is still pending: the server only marked the row read.
            m_proxy->dropOverlay(id);
            m_proxy->overlayRead(id, true);
        } else {
            m_proxy->settle(id);
        }
        refresh();
    });
}

void NotificationFeed::markRead(qint64 id)
{
    Taos::Client *c = client();
    if (!c)
        return;
    m_proxy->overlayRead(id, false);
    afterAction(Taos::Notifications(c).markRead(id), id, false, u"Could not mark as read."_s);
}

void NotificationFeed::dismiss(qint64 id)
{
    const QVariantMap row = rowById(id);
    if (isPinnedSource(row.value(u"source"_s).toString())) {
        markRead(id); // a consent row is never archived by a dismiss
        return;
    }
    Taos::Client *c = client();
    if (!c)
        return;
    m_proxy->overlayHide(id, false);
    afterAction(Taos::Notifications(c).archive(id), id, true, u"Could not dismiss."_s);
}

void NotificationFeed::resolved(qint64 id)
{
    Taos::Client *c = client();
    if (!c)
        return;
    m_proxy->overlayHide(id, false);
    afterAction(Taos::Notifications(c).archive(id), id, true, QString());
}

void NotificationFeed::markAllRead()
{
    Taos::Client *c = client();
    if (!c || !m_query)
        return;
    QList<qint64> ids;
    auto *m = m_query->model();
    for (int r = 0; r < m->rowCount(); ++r) {
        const qint64 id = qint64(m->row(r).value(u"id"_s).toDouble());
        ids << id;
        m_proxy->overlayRead(id, false);
    }
    Taos::Notifications(c).markAllRead()->then(this, [this, ids](const Taos::Result &res) {
        if (!res.ok()) {
            for (qint64 id : ids)
                m_proxy->dropOverlay(id);
            Q_EMIT actionFailed(u"Could not mark all as read. "_s + res.error.userMessage());
            return;
        }
        for (qint64 id : ids)
            m_proxy->settle(id);
        refresh();
    });
}

void NotificationFeed::clearAll()
{
    Taos::Client *c = client();
    if (!c || !m_query)
        return;
    QList<qint64> ids;
    auto *m = m_query->model();
    for (int r = 0; r < m->rowCount(); ++r) {
        const QJsonObject o = m->row(r);
        if (o.value(u"pinned"_s).toBool() || isPinnedSource(o.value(u"source"_s).toString()))
            continue; // pending consent rows stay
        const qint64 id = qint64(o.value(u"id"_s).toDouble());
        ids << id;
        m_proxy->overlayHide(id, false);
    }
    Taos::Notifications(c).clear()->then(this, [this, ids](const Taos::Result &res) {
        if (!res.ok()) {
            for (qint64 id : ids)
                m_proxy->dropOverlay(id);
            Q_EMIT actionFailed(u"Could not clear notifications. "_s + res.error.userMessage());
            return;
        }
        for (qint64 id : ids)
            m_proxy->settle(id);
        refresh();
    });
}

void NotificationFeed::requestOpen(const QString &appId, const QVariantMap &props)
{
    qInfo().noquote() << "taos: open-app request" << appId
                      << QJsonDocument(QJsonObject::fromVariantMap(props)).toJson(QJsonDocument::Compact);
    Q_EMIT openAppRequested(appId, props);
}

void NotificationFeed::activate(qint64 id)
{
    const QVariantMap t = targetFor(id);
    if (t.value(u"valid"_s).toBool())
        requestOpen(t.value(u"app"_s).toString(), t.value(u"props"_s).toMap());
    markRead(id);
}

void registerAppsQmlTypes()
{
    qmlRegisterType<NotificationFeed>("Taos.Data", 1, 0, "NotificationFeed");
}

Q_COREAPP_STARTUP_FUNCTION(registerAppsQmlTypes)

} // namespace Taos::Apps
