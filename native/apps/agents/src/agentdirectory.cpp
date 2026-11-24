#include "agentdirectory.h"

#include <QCoreApplication>
#include <QDesktopServices>
#include <QJsonArray>
#include <QProcess>
#include <QStandardPaths>
#include <QUrl>
#include <QtQml/qqml.h>

using namespace Qt::StringLiterals;

namespace Taos::Apps {

namespace {
const QStringList kRoles = {u"name"_s, u"display_name"_s, u"host"_s, u"color"_s, u"emoji"_s, u"framework"_s,
                            u"model"_s, u"state"_s, u"status"_s, u"frozen"_s, u"vectors"_s, u"paused"_s,
                            u"owner_id"_s, u"actions"_s, u"protected"_s, u"device"_s, u"remote"_s,
                            u"container"_s, u"registry_canonical_id"_s, u"memory_mode"_s,
                            u"framework_version_sha"_s};
const QStringList kVirtual = {u"title"_s, u"deviceName"_s, u"deviceKind"_s, u"deviceReach"_s, u"busy"_s,
                              u"actionError"_s, u"ownerLabel"_s, u"agentState"_s, u"chatModel"_s};
const QStringList kActions = {u"start"_s, u"stop"_s, u"restart"_s, u"pause"_s, u"resume"_s};
} // namespace

// ---- AgentProxy ----------------------------------------------------------------

AgentProxy::AgentProxy(QObject *parent) : QSortFilterProxyModel(parent)
{
    setDynamicSortFilter(true);
    setSortCaseSensitivity(Qt::CaseInsensitive);
}

void AgentProxy::setSourceModelChecked(QAbstractItemModel *m)
{
    setSourceModel(m);
    sort(0);
}

int AgentProxy::roleOf(const char *name) const
{
    const auto names = roleNames();
    for (auto it = names.cbegin(); it != names.cend(); ++it)
        if (it.value() == name)
            return it.key();
    return -1;
}

QHash<int, QByteArray> AgentProxy::roleNames() const
{
    QHash<int, QByteArray> r = sourceModel() ? sourceModel()->roleNames() : QHash<int, QByteArray>();
    for (int i = 0; i < kVirtual.size(); ++i)
        r.insert(kVirtualBase + i, kVirtual.at(i).toUtf8());
    return r;
}

QVariant AgentProxy::src(int sourceRow, const char *role) const
{
    if (!sourceModel())
        return {};
    const auto names = sourceModel()->roleNames();
    for (auto it = names.cbegin(); it != names.cend(); ++it)
        if (it.value() == role)
            return sourceModel()->data(sourceModel()->index(sourceRow, 0), it.key());
    return {};
}

QString AgentProxy::deviceLabel(const QVariantMap &d)
{
    if (d.value(u"local"_s).toBool() || d.value(u"name"_s).toString().isEmpty())
        return QObject::tr("This device");
    return d.value(u"name"_s).toString();
}

QVariant AgentProxy::data(const QModelIndex &index, int role) const
{
    if (!index.isValid() || role < kVirtualBase || role >= kVirtualBase + kVirtual.size())
        return QSortFilterProxyModel::data(index, role);
    const int sr = mapToSource(index).row();
    const QVariantMap dev = src(sr, "device").toMap();
    const QString name = src(sr, "name").toString();
    switch (role - kVirtualBase) {
    case 0: {
        const QString d = src(sr, "display_name").toString();
        return d.isEmpty() ? name : d;
    }
    case 1: return deviceLabel(dev);
    case 2: return dev.value(u"kind"_s).toString();
    case 3: return dev.value(u"reach"_s).toString();
    case 4: return m_busy.value(name);
    case 5: return m_errors.value(name);
    case 6: return src(sr, "owner_id").toString();
    case 7: return src(sr, "state").toString();
    case 8: return src(sr, "model").toString();
    }
    return {};
}

void AgentProxy::setFilters(const QString &text, const QString &state, const QString &device)
{
    m_text = text.trimmed().toLower();
    m_state = state;
    m_device = device;
    invalidateFilter();
}

void AgentProxy::setGroupByDevice(bool on)
{
    m_group = on;
    invalidate();
}

void AgentProxy::setBusy(const QString &name, const QString &action)
{
    if (action.isEmpty())
        m_busy.remove(name);
    else
        m_busy.insert(name, action);
    Q_EMIT dataChanged(index(0, 0), index(qMax(0, rowCount() - 1), 0), {kVirtualBase + 4});
}

void AgentProxy::setActionError(const QString &name, const QString &text)
{
    if (text.isEmpty())
        m_errors.remove(name);
    else
        m_errors.insert(name, text);
    Q_EMIT dataChanged(index(0, 0), index(qMax(0, rowCount() - 1), 0), {kVirtualBase + 5});
}

bool AgentProxy::filterAcceptsRow(int row, const QModelIndex &) const
{
    if (!m_state.isEmpty() && src(row, "state").toString() != m_state)
        return false;
    const QVariantMap dev = src(row, "device").toMap();
    if (!m_device.isEmpty()) {
        const bool local = dev.value(u"local"_s).toBool();
        if (m_device == u"@local"_s ? !local : (local || dev.value(u"name"_s).toString() != m_device))
            return false;
    }
    if (m_text.isEmpty())
        return true;
    const QStringList hay = {src(row, "name").toString(), src(row, "display_name").toString(),
                             src(row, "framework").toString(), src(row, "model").toString(),
                             deviceLabel(dev), src(row, "owner_id").toString()};
    for (const QString &h : hay)
        if (h.toLower().contains(m_text))
            return true;
    return false;
}

bool AgentProxy::lessThan(const QModelIndex &l, const QModelIndex &r) const
{
    if (m_group) {
        const QVariantMap dl = src(l.row(), "device").toMap(), dr = src(r.row(), "device").toMap();
        const bool ll = dl.value(u"local"_s).toBool(), lr = dr.value(u"local"_s).toBool();
        if (ll != lr)
            return ll; // this device first
        const int c = dl.value(u"name"_s).toString().compare(dr.value(u"name"_s).toString(), Qt::CaseInsensitive);
        if (c != 0)
            return c < 0;
    }
    auto title = [this](int row) {
        const QString d = src(row, "display_name").toString();
        return d.isEmpty() ? src(row, "name").toString() : d;
    };
    const int c = title(l.row()).compare(title(r.row()), Qt::CaseInsensitive);
    if (c != 0)
        return c < 0;
    return src(l.row(), "name").toString() < src(r.row(), "name").toString();
}

// ---- AgentDirectory --------------------------------------------------------------

AgentDirectory::AgentDirectory(QObject *parent) : QObject(parent), m_proxy(new AgentProxy(this))
{
    m_poll.setInterval(5000);
    connect(&m_poll, &QTimer::timeout, this, [this] {
        if (m_running && m_query && !m_query->loading())
            m_query->refresh();
    });
}

Taos::Client *AgentDirectory::client() const
{
    if (m_client)
        return m_client.data();
    if (m_session)
        return m_session->client();
    return Taos::defaultClient();
}

void AgentDirectory::setSession(Taos::Session *s)
{
    if (m_session == s)
        return;
    if (m_session)
        disconnect(m_session, nullptr, this, nullptr);
    m_session = s;
    if (s)
        connect(s, &Taos::Session::userChanged, this, [this] {
            // Admins also get the owner of every agent.
            if (m_query) {
                m_query->setArgs({{u"with_owners"_s, isAdmin()}});
                m_query->refresh();
            }
            Q_EMIT stateChanged();
        });
    Q_EMIT sessionChanged();
}

bool AgentDirectory::isAdmin() const
{
    return m_session && m_session->user().value(u"is_admin"_s).toBool();
}

void AgentDirectory::ensureQuery()
{
    if (m_query)
        return;
    Taos::Client *c = client();
    if (!c)
        return;
    m_query = new Taos::LiveQuery(c, u"agents.list"_s, {{u"with_owners"_s, isAdmin()}},
                                  {u"agents.status.changed"_s}, kRoles, this, u"name"_s);
    m_proxy->setSourceModelChecked(m_query->model());
    connect(m_query, &Taos::LiveQuery::loaded, this, &AgentDirectory::onLoaded);
    connect(m_query, &Taos::LiveQuery::failed, this, &AgentDirectory::onFailed);
    connect(m_query, &Taos::LiveQuery::loadingChanged, this, &AgentDirectory::stateChanged);
    connect(m_query, &Taos::LiveQuery::liveChanged, this, &AgentDirectory::stateChanged);
    connect(m_proxy, &QAbstractItemModel::rowsInserted, this, &AgentDirectory::viewChanged);
    connect(m_proxy, &QAbstractItemModel::rowsRemoved, this, &AgentDirectory::viewChanged);
    connect(m_proxy, &QAbstractItemModel::modelReset, this, &AgentDirectory::viewChanged);
}

void AgentDirectory::setRunning(bool on)
{
    if (m_running == on)
        return;
    m_running = on;
    if (on) {
        ensureQuery();
        if (m_query)
            m_query->start();
        loadTaosAgent();
        m_poll.start();
    } else {
        m_poll.stop();
        if (m_query)
            m_query->stop();
    }
    Q_EMIT runningChanged();
}

void AgentDirectory::refresh()
{
    if (m_query)
        m_query->refresh();
    loadTaosAgent();
}

void AgentDirectory::onLoaded()
{
    m_loaded = true;
    m_errorText.clear();
    m_errorKind.clear();
    ++m_revision;
    Q_EMIT stateChanged();
    Q_EMIT viewChanged();
}

void AgentDirectory::onFailed(const Taos::Error &e)
{
    // A failed refresh keeps the last list; the window says so.
    m_errorText = e.userMessage();
    switch (e.kind()) {
    case Taos::Error::Forbidden: m_errorKind = u"forbidden"_s; break;
    case Taos::Error::Unauthenticated: m_errorKind = u"unauthenticated"_s; break;
    case Taos::Error::Network: m_errorKind = u"network"_s; break;
    default: m_errorKind = u"other"_s; break;
    }
    Q_EMIT stateChanged();
}

void AgentDirectory::loadTaosAgent()
{
    Taos::Client *c = client();
    if (!c)
        return;
    c->call(u"agents.taos_agent"_s)->then(this, [this](const Taos::Result &r) {
        if (!r.ok())
            return; // the pinned row then shows without a model
        const QJsonObject o = r.value.toObject();
        m_taos = {{u"name"_s, u"taos-agent"_s},
                  {u"model"_s, o.value(u"model"_s).toString()},
                  {u"framework"_s, o.value(u"framework"_s).toString()},
                  {u"permitted_models"_s, o.value(u"permitted_models"_s).toArray().toVariantList()}};
        Q_EMIT taosAgentChanged();
    });
}

int AgentDirectory::totalCount() const { return m_query ? m_query->model()->rowCount() : 0; }

void AgentDirectory::setFilterText(const QString &t)
{
    if (m_text == t)
        return;
    m_text = t;
    m_proxy->setFilters(m_text, m_state, m_device);
    Q_EMIT viewChanged();
}
void AgentDirectory::setStateFilter(const QString &s)
{
    if (m_state == s)
        return;
    m_state = s;
    m_proxy->setFilters(m_text, m_state, m_device);
    Q_EMIT viewChanged();
}
void AgentDirectory::setDeviceFilter(const QString &d)
{
    if (m_device == d)
        return;
    m_device = d;
    m_proxy->setFilters(m_text, m_state, m_device);
    Q_EMIT viewChanged();
}
void AgentDirectory::setGroupByDevice(bool on)
{
    if (m_group == on)
        return;
    m_group = on;
    m_proxy->setGroupByDevice(on);
    Q_EMIT viewChanged();
}

QVariantList AgentDirectory::devices() const
{
    // [{value, label, kind, reach, count}], this device first, then by name.
    QMap<QString, QVariantMap> by;
    if (m_query) {
        auto *m = m_query->model();
        for (int i = 0; i < m->rowCount(); ++i) {
            const QVariantMap d = QJsonObject(m->row(i).value(u"device"_s).toObject()).toVariantMap();
            const bool local = d.value(u"local"_s).toBool();
            const QString key = local ? u"@local"_s : d.value(u"name"_s).toString();
            QVariantMap e = by.value(key);
            e.insert(u"value"_s, key);
            e.insert(u"label"_s, AgentProxy::deviceLabel(d));
            e.insert(u"kind"_s, d.value(u"kind"_s));
            e.insert(u"reach"_s, d.value(u"reach"_s));
            e.insert(u"count"_s, e.value(u"count"_s).toInt() + 1);
            by.insert(key, e);
        }
    }
    QVariantList out;
    if (by.contains(u"@local"_s))
        out << by.take(u"@local"_s);
    for (auto it = by.cbegin(); it != by.cend(); ++it)
        out << it.value();
    return out;
}

QVariantMap AgentDirectory::stateCounts() const
{
    QVariantMap out;
    if (!m_query)
        return out;
    auto *m = m_query->model();
    for (int i = 0; i < m->rowCount(); ++i) {
        const QString s = m->row(i).value(u"state"_s).toString();
        out.insert(s, out.value(s).toInt() + 1);
    }
    return out;
}

QVariantMap AgentDirectory::agent(const QString &name) const
{
    if (!m_query)
        return {};
    auto *m = m_query->model();
    for (int i = 0; i < m->rowCount(); ++i)
        if (m->row(i).value(u"name"_s).toString() == name)
            return m->row(i).toVariantMap();
    return {};
}

QString AgentDirectory::progressLabel(const QString &action)
{
    if (action == u"start")
        return tr("Starting");
    if (action == u"stop")
        return tr("Stopping");
    if (action == u"restart")
        return tr("Restarting");
    if (action == u"pause")
        return tr("Pausing");
    if (action == u"resume")
        return tr("Resuming");
    return {};
}

QString AgentDirectory::refusalText(const Taos::Error &e, const QString &action) const
{
    switch (e.kind()) {
    case Taos::Error::Forbidden:
        return tr("You are not allowed to %1 this agent. The controller said: %2").arg(action, e.userMessage());
    case Taos::Error::NotFound:
        return tr("The controller cannot find this agent for you. %1").arg(e.userMessage());
    default:
        return e.userMessage();
    }
}

void AgentDirectory::act(const QString &name, const QString &action)
{
    Taos::Client *c = client();
    if (!c || !kActions.contains(action) || m_busy.contains(name))
        return;
    m_busy.insert(name, action);
    m_errors.remove(name);
    m_proxy->setBusy(name, action);
    m_proxy->setActionError(name, {});
    ++m_revision;
    Q_EMIT stateChanged();
    c->call(u"agents.act"_s, {{u"name"_s, name}, {u"action"_s, action}})
        ->then(this, [this, name, action](const Taos::Result &r) {
            QString text;
            bool ok = r.ok();
            if (!ok)
                text = refusalText(r.error, action);
            else if (!r.value.toObject().value(u"ok"_s).toBool(true)) {
                ok = false;
                text = r.value.toObject().value(u"message"_s).toString();
            }
            m_busy.remove(name);
            m_proxy->setBusy(name, {});
            if (!ok)
                m_proxy->setActionError(name, text), m_errors.insert(name, text);
            ++m_revision;
            Q_EMIT stateChanged();
            Q_EMIT actionFinished(name, action, ok, text);
            // Refetch first so the controls come back already showing the new state.
            refresh();
        });
}

void AgentDirectory::clearActionError(const QString &name)
{
    m_errors.remove(name);
    m_proxy->setActionError(name, {});
    ++m_revision;
    Q_EMIT stateChanged();
}

bool AgentDirectory::canMessage() const
{
    return !QStandardPaths::findExecutable(u"taos-messages"_s, {QCoreApplication::applicationDirPath()}).isEmpty()
        || !QStandardPaths::findExecutable(u"taos-messages"_s).isEmpty();
}

bool AgentDirectory::messageAgent(const QString &name)
{
    QString exe = QStandardPaths::findExecutable(u"taos-messages"_s, {QCoreApplication::applicationDirPath()});
    if (exe.isEmpty())
        exe = QStandardPaths::findExecutable(u"taos-messages"_s);
    if (exe.isEmpty()) {
        Q_EMIT launchFailed(tr("The Messages app is not installed on this device."));
        return false;
    }
    if (!QProcess::startDetached(exe, {u"--agent"_s, name})) {
        Q_EMIT launchFailed(tr("Could not start the Messages app."));
        return false;
    }
    return true;
}

QString AgentDirectory::controllerUrl() const
{
    return qEnvironmentVariableIsSet("TAOS_URL") ? qEnvironmentVariable("TAOS_URL")
                                                 : QStringLiteral("http://127.0.0.1:6969");
}

bool AgentDirectory::openWebWizard()
{
    return QDesktopServices::openUrl(QUrl(controllerUrl()));
}

void AgentDirectory::loadActivity(const QString &name)
{
    Taos::Client *c = client();
    if (!c)
        return;
    m_activityFor = name;
    m_activityState = u"loading"_s;
    m_activity.clear();
    Q_EMIT activityChanged();
    c->http(u"GET"_s, u"/api/agents/"_s + QString::fromUtf8(QUrl::toPercentEncoding(name)) + u"/trace"_s,
            {{u"limit"_s, 6}})
        ->then(this, [this, name](const Taos::Result &r) {
            if (name != m_activityFor)
                return; // another agent was opened meanwhile
            m_activity.clear();
            if (!r.ok()) {
                m_activityState = r.error.kind() == Taos::Error::Forbidden ? u"forbidden"_s : u"error"_s;
            } else {
                const QJsonArray ev = r.value.toObject().value(u"events"_s).toArray();
                for (const QJsonValue &v : ev) {
                    const QJsonObject o = v.toObject();
                    m_activity << QVariantMap{{u"kind"_s, o.value(u"kind"_s).toString()},
                                              {u"at"_s, o.value(u"created_at"_s).toDouble()},
                                              {u"model"_s, o.value(u"model"_s).toString()},
                                              {u"error"_s, o.value(u"error"_s).toString()}};
                }
                m_activityState = m_activity.isEmpty() ? u"empty"_s : u"ready"_s;
            }
            Q_EMIT activityChanged();
        });
}

void AgentDirectory::clearActivity()
{
    m_activityFor.clear();
    m_activity.clear();
    m_activityState.clear();
    Q_EMIT activityChanged();
}

void registerAgentsQmlTypes()
{
    qmlRegisterType<AgentDirectory>("Taos.Data", 1, 0, "AgentDirectory");
}

Q_COREAPP_STARTUP_FUNCTION(registerAgentsQmlTypes)

} // namespace Taos::Apps
