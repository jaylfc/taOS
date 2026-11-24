#pragma once
// The engine of the native Agents app: the agent list (a LiveQuery over
// `agents.list`), its filtered and grouped view, per-agent action state and the
// few things a window cannot do from QML (launch the Messages app, open the web
// wizard). A plain QObject with no UI; it holds no state the controller would
// not also hold, apart from the in-flight and last-failure state of an action.
#include <QJsonObject>
#include <QPointer>
#include <QSortFilterProxyModel>
#include <QTimer>
#include <QVariantList>
#include <QVariantMap>

#include "taosqt/client.h"
#include "taosqt/livequery.h"
#include "taosqt/session.h"

namespace Taos::Apps {

/// The list the window shows: text search, state and device filters, optional
/// grouping by device (this device first, then by device name), then by name.
/// Adds the virtual roles `title`, `deviceName`, `deviceKind`, `deviceReach`,
/// `busy` (the action in flight, or ""), `actionError` and `ownerLabel`.
class AgentProxy : public QSortFilterProxyModel
{
    Q_OBJECT
public:
    explicit AgentProxy(QObject *parent = nullptr);
    void setSourceModelChecked(QAbstractItemModel *m);
    void setFilters(const QString &text, const QString &state, const QString &device);
    void setGroupByDevice(bool on);
    void setBusy(const QString &name, const QString &action);
    void setActionError(const QString &name, const QString &text);
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    int roleOf(const char *name) const;
    static QString deviceLabel(const QVariantMap &device);

protected:
    bool lessThan(const QModelIndex &l, const QModelIndex &r) const override;
    bool filterAcceptsRow(int row, const QModelIndex &parent) const override;

private:
    QVariant src(int sourceRow, const char *role) const;
    QString m_text, m_state, m_device;
    bool m_group = false;
    QHash<QString, QString> m_busy, m_errors;
    static constexpr int kVirtualBase = Qt::UserRole + 200;
};

class AgentDirectory : public QObject
{
    Q_OBJECT
    Q_PROPERTY(Taos::Session *session READ session WRITE setSession NOTIFY sessionChanged)
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    Q_PROPERTY(QAbstractItemModel *model READ model CONSTANT)
    Q_PROPERTY(bool loaded READ loaded NOTIFY stateChanged)
    Q_PROPERTY(bool loading READ loading NOTIFY stateChanged)
    Q_PROPERTY(bool live READ live NOTIFY stateChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY stateChanged)
    /// "", "forbidden", "unauthenticated", "network" or "other".
    Q_PROPERTY(QString errorKind READ errorKind NOTIFY stateChanged)
    Q_PROPERTY(int totalCount READ totalCount NOTIFY stateChanged)
    Q_PROPERTY(int shownCount READ shownCount NOTIFY viewChanged)
    Q_PROPERTY(bool isAdmin READ isAdmin NOTIFY stateChanged)
    Q_PROPERTY(QString filterText READ filterText WRITE setFilterText NOTIFY viewChanged)
    Q_PROPERTY(QString stateFilter READ stateFilter WRITE setStateFilter NOTIFY viewChanged)
    Q_PROPERTY(QString deviceFilter READ deviceFilter WRITE setDeviceFilter NOTIFY viewChanged)
    Q_PROPERTY(bool groupByDevice READ groupByDevice WRITE setGroupByDevice NOTIFY viewChanged)
    /// Device names present in the list (empty string is this device), for the filter.
    Q_PROPERTY(QVariantList devices READ devices NOTIFY stateChanged)
    Q_PROPERTY(QVariantMap stateCounts READ stateCounts NOTIFY stateChanged)
    /// Bumped whenever the rows change, so QML bindings that call agent() re-run.
    Q_PROPERTY(int revision READ revision NOTIFY stateChanged)
    Q_PROPERTY(QVariantMap taosAgent READ taosAgent NOTIFY taosAgentChanged)
    Q_PROPERTY(bool canMessage READ canMessage CONSTANT)
    Q_PROPERTY(QString controllerUrl READ controllerUrl CONSTANT)
    Q_PROPERTY(QVariantList activity READ activity NOTIFY activityChanged)
    Q_PROPERTY(QString activityState READ activityState NOTIFY activityChanged)
public:
    explicit AgentDirectory(QObject *parent = nullptr);
    void setClientForTest(Taos::Client *c) { m_client = c; }

    Taos::Session *session() const { return m_session; }
    void setSession(Taos::Session *s);
    bool running() const { return m_running; }
    void setRunning(bool on);
    QAbstractItemModel *model() const { return m_proxy; }
    bool loaded() const { return m_loaded; }
    bool loading() const { return m_query && m_query->loading(); }
    bool live() const { return m_query && m_query->live(); }
    QString errorText() const { return m_errorText; }
    QString errorKind() const { return m_errorKind; }
    int totalCount() const;
    int shownCount() const { return m_proxy->rowCount(); }
    bool isAdmin() const;
    QString filterText() const { return m_text; }
    void setFilterText(const QString &t);
    QString stateFilter() const { return m_state; }
    void setStateFilter(const QString &s);
    QString deviceFilter() const { return m_device; }
    void setDeviceFilter(const QString &d);
    bool groupByDevice() const { return m_group; }
    void setGroupByDevice(bool on);
    QVariantList devices() const;
    QVariantMap stateCounts() const;
    int revision() const { return m_revision; }
    QVariantMap taosAgent() const { return m_taos; }
    bool canMessage() const;
    QString controllerUrl() const;
    QVariantList activity() const { return m_activity; }
    QString activityState() const { return m_activityState; }

    /// The row of agent `name` (empty map when it is not in the list).
    Q_INVOKABLE QVariantMap agent(const QString &name) const;
    /// Run a lifecycle action. Ignored while one is in flight for the agent.
    Q_INVOKABLE void act(const QString &name, const QString &action);
    Q_INVOKABLE bool isBusy(const QString &name) const { return m_busy.contains(name); }
    Q_INVOKABLE QString busyAction(const QString &name) const { return m_busy.value(name); }
    Q_INVOKABLE QString actionError(const QString &name) const { return m_errors.value(name); }
    Q_INVOKABLE void clearActionError(const QString &name);
    Q_INVOKABLE void refresh();
    /// Launch `taos-messages --agent <name>`; false when that binary is not installed.
    Q_INVOKABLE bool messageAgent(const QString &name);
    /// Open the web app (where the deploy wizard is) in the default browser.
    Q_INVOKABLE bool openWebWizard();
    /// Load the last few trace events of an agent into `activity`.
    Q_INVOKABLE void loadActivity(const QString &name);
    Q_INVOKABLE void clearActivity();
    /// A short label for a lifecycle action in progress, for example "Stopping".
    Q_INVOKABLE static QString progressLabel(const QString &action);

Q_SIGNALS:
    void sessionChanged();
    void runningChanged();
    void stateChanged();
    void viewChanged();
    void taosAgentChanged();
    void activityChanged();
    /// `ok` false: `text` is the sentence to show (also kept as actionError).
    void actionFinished(const QString &name, const QString &action, bool ok, const QString &text);
    void launchFailed(const QString &text);

private:
    Taos::Client *client() const;
    void ensureQuery();
    void onFailed(const Taos::Error &e);
    void onLoaded();
    void loadTaosAgent();
    QString refusalText(const Taos::Error &e, const QString &action) const;

    QPointer<Taos::Client> m_client;
    QPointer<Taos::Session> m_session;
    Taos::LiveQuery *m_query = nullptr;
    AgentProxy *m_proxy;
    QTimer m_poll;
    bool m_running = false, m_loaded = false, m_group = false;
    QString m_errorText, m_errorKind, m_text, m_state, m_device;
    QHash<QString, QString> m_busy, m_errors;
    QVariantMap m_taos;
    QVariantList m_activity;
    QString m_activityState, m_activityFor;
    int m_revision = 0;
};

void registerAgentsQmlTypes();

} // namespace Taos::Apps
