// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The notification store of the native apps: the engine behind the Notifications
// app, and what the bar's bell reads later (it is a plain QObject over the
// foundation's LiveQuery; it has no UI and no state the controller would not
// also hold, apart from a short optimistic overlay while a POST is in flight).
#include <QJsonObject>
#include <QPointer>
#include <QSet>
#include <QSortFilterProxyModel>
#include <QStringList>
#include <QVariantMap>

#include "taosqt/client.h"
#include "taosqt/livequery.h"

namespace Taos::Apps {

/// Where a notification points. `app` is a native app id (`my.taos.<web registry id>`),
/// empty when the row has no destination.
struct OpenTarget
{
    QString app;
    QVariantMap props;
    bool valid() const { return !app.isEmpty(); }
};

/// The routing table of the web app (`lib/server-notifications.ts`, `targetToAction`
/// then `sourceToTarget`), mapped onto native app ids.
OpenTarget routeFor(const QString &source, const QJsonObject &data);

/// Sources whose rows are consent requests: pinned first, never archived by a dismiss.
bool isPinnedSource(const QString &source);

/// Pinned rows first (newest first), then the rest newest first, with the 100 cap
/// applied to the rest only (`notification-store.ts:76-103`). Filters by level,
/// source and unread. Rows hidden or marked read by the overlay follow the overlay.
class NotificationProxy : public QSortFilterProxyModel
{
    Q_OBJECT
public:
    explicit NotificationProxy(QObject *parent = nullptr);
    void setSourceModelChecked(QAbstractItemModel *m);
    void setFilters(const QString &level, const QString &source, bool unreadOnly);
    void overlayRead(qint64 id, bool settled);
    void overlayHide(qint64 id, bool settled);
    void dropOverlay(qint64 id);
    void settle(qint64 id);
    void pruneSettled();
    int unreadCount() const;
    int pinnedCount() const;
    int totalCount() const; ///< rows visible with no filter
    QStringList sources() const;
    bool isRead(const QModelIndex &sourceIndex) const;
    int roleOf(const char *name) const;
    QVariant data(const QModelIndex &index, int role) const override;
    static constexpr int kRestCap = 100;

Q_SIGNALS:
    void countsChanged();

protected:
    bool lessThan(const QModelIndex &l, const QModelIndex &r) const override;
    bool filterAcceptsRow(int row, const QModelIndex &parent) const override;

private:
    void refilter();
    void scheduleRebuild();
    void rebuild();
    qint64 idAt(int sourceRow) const;
    bool pinnedAt(int sourceRow) const;
    qint64 stampAt(int sourceRow) const;

    QString m_level, m_source;
    bool m_unreadOnly = false;
    QSet<qint64> m_readOverlay, m_hiddenOverlay, m_settledRead, m_settledHidden;
    QSet<qint64> m_allowed; ///< ids inside the cap
    bool m_rebuildQueued = false;
    int m_unread = 0, m_pinned = 0, m_total = 0;
    QStringList m_sources;
};

class NotificationFeed : public QObject
{
    Q_OBJECT
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    Q_PROPERTY(QAbstractItemModel *model READ model CONSTANT)
    Q_PROPERTY(bool loading READ loading NOTIFY stateChanged)
    Q_PROPERTY(bool loaded READ loaded NOTIFY stateChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY stateChanged)
    Q_PROPERTY(bool live READ live NOTIFY stateChanged)
    Q_PROPERTY(int unreadCount READ unreadCount NOTIFY countsChanged)
    Q_PROPERTY(int pinnedCount READ pinnedCount NOTIFY countsChanged)
    Q_PROPERTY(int totalCount READ totalCount NOTIFY countsChanged)
    Q_PROPERTY(QStringList sources READ sources NOTIFY countsChanged)
    Q_PROPERTY(QString levelFilter READ levelFilter WRITE setLevelFilter NOTIFY filtersChanged)
    Q_PROPERTY(QString sourceFilter READ sourceFilter WRITE setSourceFilter NOTIFY filtersChanged)
    Q_PROPERTY(bool unreadOnly READ unreadOnly WRITE setUnreadOnly NOTIFY filtersChanged)
public:
    explicit NotificationFeed(QObject *parent = nullptr);
    NotificationFeed(Taos::Client *client, QObject *parent);

    bool running() const { return m_running; }
    void setRunning(bool on);
    QAbstractItemModel *model() const { return m_proxy; }
    bool loading() const { return m_query && m_query->loading(); }
    bool loaded() const { return m_loadedOnce; }
    QString errorText() const { return m_query ? m_query->errorText() : QString(); }
    bool live() const { return m_query && m_query->live(); }
    int unreadCount() const { return m_proxy->unreadCount(); }
    int pinnedCount() const { return m_proxy->pinnedCount(); }
    int totalCount() const { return m_proxy->totalCount(); }
    QStringList sources() const { return m_proxy->sources(); }
    QString levelFilter() const { return m_level; }
    void setLevelFilter(const QString &v);
    QString sourceFilter() const { return m_source; }
    void setSourceFilter(const QString &v);
    bool unreadOnly() const { return m_unreadOnly; }
    void setUnreadOnly(bool v);

    /// Server routes used (all through the foundation's typed calls):
    /// POST /api/notifications/{id}/read, /archive, /read-all; GET /api/notifications.
    Q_INVOKABLE void markRead(qint64 id);
    /// The web store's dismiss: a consent row is only marked read, any other row is archived.
    Q_INVOKABLE void dismiss(qint64 id);
    /// A consent or sign-in row was resolved: archive it (web `archiveRead`).
    Q_INVOKABLE void resolved(qint64 id);
    Q_INVOKABLE void markAllRead();
    /// Archive everything except pending consent rows (`notifications.clear`).
    Q_INVOKABLE void clearAll();
    Q_INVOKABLE void refresh();
    /// Row tap: open the target app (a request for the shell, logged) and mark read.
    Q_INVOKABLE void activate(qint64 id);
    Q_INVOKABLE QVariantMap targetFor(qint64 id) const;
    /// A request for the shell to open `appId` (logged; the shell handles `openAppRequested`).
    Q_INVOKABLE void requestOpen(const QString &appId, const QVariantMap &props = {});
    Q_INVOKABLE QVariantMap rowById(qint64 id) const;

Q_SIGNALS:
    void runningChanged();
    void stateChanged();
    void countsChanged();
    void filtersChanged();
    /// The shell handles this: open `appId` with launch `props`.
    void openAppRequested(const QString &appId, const QVariantMap &props);
    void actionFailed(const QString &text);
    void notice(const QString &text);

private:
    Taos::Client *client() const;
    void ensureQuery();
    void afterAction(Taos::Reply *r, qint64 id, bool hide, const QString &failText);

    QPointer<Taos::Client> m_client;
    Taos::LiveQuery *m_query = nullptr;
    NotificationProxy *m_proxy;
    bool m_running = false;
    bool m_loadedOnce = false;
    QString m_level, m_source;
    bool m_unreadOnly = false;
};

void registerAppsQmlTypes();

} // namespace Taos::Apps
