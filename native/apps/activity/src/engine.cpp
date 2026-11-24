// SPDX-License-Identifier: AGPL-3.0-or-later
#include "engine.h"

#include <QJsonArray>
#include <cmath>

#include "fmt.h"
#include "taosqt/error.h"

using namespace Qt::StringLiterals;

namespace Taos::Activity {

namespace {
// The controller sends `{}` or null where a list is expected on its error
// paths (zram, network, per-core arrays). Normalise those to empty lists so the
// views can iterate without checking.
void listify(QVariantMap &m, const QString &key)
{
    if (m.contains(key) && m.value(key).typeId() != QMetaType::QVariantList)
        m[key] = QVariantList();
}
void sanitize(QVariantMap &a)
{
    for (const QString &k : {u"thermal"_s, u"zram"_s, u"network"_s, u"processes"_s})
        listify(a, k);
    for (const QString &blk : {u"cpu"_s, u"npu"_s}) {
        QVariantMap b = a.value(blk).toMap();
        listify(b, u"cores"_s);
        a[blk] = b;
    }
}
// Poll cadence of the web app (ActivityApp.tsx): 2 s batch, 10 s workers.
constexpr int kPollMs = 2000;
constexpr int kWorkersMs = 10000;
} // namespace

ActivityEngine::ActivityEngine(QObject *parent) : QObject(parent)
{
    m_poll.setInterval(kPollMs);
    m_workersTimer.setInterval(kWorkersMs);
    connect(&m_poll, &QTimer::timeout, this, &ActivityEngine::fetchBatch);
    connect(&m_workersTimer, &QTimer::timeout, this, &ActivityEngine::refreshWorkers);
}

ActivityEngine::~ActivityEngine() = default;

Client *ActivityEngine::client() const
{
    return m_client ? m_client.data() : defaultClient();
}

void ActivityEngine::setClient(Client *c)
{
    if (m_client == c)
        return;
    m_client = c;
    Q_EMIT clientChanged();
}

QString ActivityEngine::restartSummary()
{
    // routes/system.py restart_ai_stack: the targets are the managed AI backend
    // services installed on the controller's own node (qmd, plus every store
    // backend declared auto-managed, for example rkllama). The controller, the
    // desktop, agents and cluster workers are not touched.
    return tr("This restarts the managed AI backend services installed on the controller: qmd, and any "
              "other backend taOS manages for local models, such as rkllama. Running inference and "
              "generation on this device is interrupted. The controller, the desktop and your agents "
              "stay up, and cluster workers are not affected.");
}

void ActivityEngine::setActive(bool on)
{
    if (m_active == on)
        return;
    m_active = on;
    if (on) {
        m_poll.start();
        m_workersTimer.start();
        refresh(); // visible again (or first show): do not wait a full interval
    } else {
        m_poll.stop();
        m_workersTimer.stop();
    }
    Q_EMIT activeChanged();
}

void ActivityEngine::refresh()
{
    fetchBatch();
    refreshWorkers();
}

void ActivityEngine::setPhase(Phase p)
{
    if (m_phase == p)
        return;
    m_phase = p;
    Q_EMIT phaseChanged();
}

void ActivityEngine::setError(const QString &e)
{
    if (m_error == e)
        return;
    m_error = e;
    Q_EMIT errorChanged();
}

void ActivityEngine::fetchBatch()
{
    Client *c = client();
    if (!c)
        return;
    if (m_inFlight > 0) { // one batch at a time; a request made meanwhile runs right after
        m_again = true;
        return;
    }
    m_pending = Pending{};
    m_inFlight = 4;
    Q_EMIT refreshingChanged();
    const struct { const char *path; } paths[4] = {
        {"/api/activity"}, {"/api/models/loaded"}, {"/api/scheduler/stats"}, {"/api/scheduler/tasks"}};
    for (int i = 0; i < 4; ++i) {
        QJsonObject query;
        if (i == 3)
            query.insert(u"limit"_s, kTaskLimit);
        c->http(u"GET"_s, QString::fromLatin1(paths[i].path), query)->then(this, [this, i](const Result &r) {
            m_pending.ok[i] = r.ok();
            m_pending.v[i] = r.value;
            m_pending.e[i] = r.error;
            if (--m_inFlight == 0)
                finishBatch();
        });
    }
}

void ActivityEngine::pushHistory(const QString &key, double v)
{
    QVariantList l = m_history.value(key).toList();
    l << v;
    while (l.size() > kHistory)
        l.removeFirst();
    m_history[key] = l;
}

void ActivityEngine::finishBatch()
{
    m_now = QDateTime::currentSecsSinceEpoch();
    // Only /api/activity decides success; the others fail on their own and
    // keep their last good data.
    m_activityOk = m_pending.ok[0] && m_pending.v[0].isObject();
    if (m_activityOk) {
        m_activity = m_pending.v[0].toObject().toVariantMap();
        sanitize(m_activity);
        m_gpus = Fmt::gpus(m_activity);
        setError(QString());
        setPhase(Ready);

        const QVariantMap cpu = m_activity.value(u"cpu"_s).toMap();
        pushHistory(u"cpu"_s, cpu.value(u"overall_percent"_s).toDouble());
        pushHistory(u"mem"_s, m_activity.value(u"memory"_s).toMap().value(u"percent"_s).toDouble());
        const QVariantMap npu = m_activity.value(u"npu"_s).toMap();
        const QVariantList npuCores = npu.value(u"cores"_s).toList();
        if (!npuCores.isEmpty()) {
            double sum = 0;
            for (const QVariant &c : npuCores)
                sum += c.toMap().value(u"load_percent"_s).toDouble();
            pushHistory(u"npu"_s, sum / npuCores.size());
        }
        for (const QVariant &gv : std::as_const(m_gpus)) {
            const QVariantMap g = gv.toMap();
            const QString i = QString::number(g.value(u"index"_s).toInt());
            if (g.value(u"loadPercent"_s).toDouble() >= 0)
                pushHistory(u"gpu."_s + i + u".load"_s, g.value(u"loadPercent"_s).toDouble());
            if (g.value(u"vramPercent"_s).toDouble() >= 0)
                pushHistory(u"gpu."_s + i + u".vram"_s, g.value(u"vramPercent"_s).toDouble());
        }
        const QVariantMap io = m_activity.value(u"disk"_s).toMap().value(u"io_rate"_s).toMap();
        pushHistory(u"diskRead"_s, io.value(u"read_bps"_s).toDouble());
        pushHistory(u"diskWrite"_s, io.value(u"write_bps"_s).toDouble());
        double rx = 0, tx = 0;
        for (const QVariant &nv : m_activity.value(u"network"_s).toList()) {
            const QVariantMap n = nv.toMap();
            if (n.value(u"name"_s).toString() == u"lo"_s)
                continue;
            rx += n.value(u"rx_bps"_s).toDouble();
            tx += n.value(u"tx_bps"_s).toDouble();
        }
        pushHistory(u"netRx"_s, rx);
        pushHistory(u"netTx"_s, tx);
        double hot = -1;
        for (const QVariant &tv : m_activity.value(u"thermal"_s).toList())
            hot = std::max(hot, tv.toMap().value(u"temp_c"_s).toDouble());
        if (hot >= 0)
            pushHistory(u"temp"_s, hot);
    } else {
        setError(m_pending.e[0].isError() ? m_pending.e[0].userMessage() : tr("The controller sent no activity data."));
        if (m_phase == Loading)
            setPhase(Unavailable);
    }
    if (m_pending.ok[1] && m_pending.v[1].isObject())
        m_localLoaded = m_pending.v[1].toObject().value(u"loaded"_s).toArray().toVariantList();
    if (m_pending.ok[2] && m_pending.v[2].isObject()) {
        m_scheduler = m_pending.v[2].toObject().toVariantMap();
        m_hasScheduler = true;
    }
    if (m_pending.ok[3] && m_pending.v[3].isObject())
        m_tasks = m_pending.v[3].toObject().value(u"tasks"_s).toArray().toVariantList().mid(0, kTaskLimit);
    m_loaded = Fmt::mergeLoaded(m_localLoaded, m_workersRaw);
    rebuildWorkers(); // heartbeat ages move with time
    Q_EMIT refreshingChanged();
    Q_EMIT dataChanged();
    Q_EMIT batchFinished(m_activityOk);
    if (m_again) {
        m_again = false;
        fetchBatch();
    }
}

void ActivityEngine::refreshWorkers()
{
    Client *c = client();
    if (!c || m_workersInFlight)
        return;
    m_workersInFlight = true;
    Q_EMIT refreshingChanged();
    c->http(u"GET"_s, u"/api/cluster/workers"_s)->then(this, [this](const Result &r) {
        m_workersInFlight = false;
        const bool ok = r.ok() && r.value.isArray(); // best effort: a failure keeps the last list
        if (ok) {
            m_workersRaw = r.value.toArray().toVariantList();
            m_loaded = Fmt::mergeLoaded(m_localLoaded, m_workersRaw);
            rebuildWorkers();
            Q_EMIT dataChanged();
        }
        Q_EMIT refreshingChanged();
        Q_EMIT workersFinished(ok);
    });
}

void ActivityEngine::rebuildWorkers()
{
    const double now = QDateTime::currentMSecsSinceEpoch() / 1000.0;
    QVariantList out;
    for (const QVariant &v : std::as_const(m_workersRaw)) {
        QVariantMap w = v.toMap();
        int state = Fmt::heartbeatState(w.value(u"last_heartbeat"_s).toDouble(), now);
        if (state == 3) {
            // The reduced and cluster_read tiers carry a server-computed
            // `status` and no heartbeat: use it instead of "unknown".
            const QString s = w.value(u"status"_s).toString();
            if (s == u"online"_s) state = 0;
            else if (s == u"stale"_s) state = 1;
            else if (s == u"offline"_s) state = 2;
        }
        w[u"state"_s] = state;
        w[u"stateLabel"_s] = Fmt::stateLabel(state);
        QString host = w.value(u"url"_s).toString();
        const QUrl u(host);
        w[u"shortHost"_s] = u.isValid() && !u.host().isEmpty()
            ? (u.port() > 0 ? u.host() + u":"_s + QString::number(u.port()) : u.host()) : host;
        // Latent capabilities: potential, not already current, and only for
        // workers that report a hardware tier.
        const QVariantList caps = w.value(u"capabilities"_s).toList();
        QVariantList latent;
        if (!w.value(u"tier_id"_s).toString().isEmpty())
            for (const QVariant &p : w.value(u"potential_capabilities"_s).toList())
                if (!caps.contains(p))
                    latent << p;
        w[u"latent"_s] = latent;
        QVariantList backends;
        for (const QVariant &bv : w.value(u"backends"_s).toList()) {
            QVariantMap b = bv.toMap();
            QString n = b.value(u"name"_s).toString();
            if (n.isEmpty()) n = b.value(u"type"_s).toString();
            if (n.isEmpty()) n = u"backend"_s;
            b[u"display"_s] = Fmt::backendName(n);
            backends << b;
        }
        w[u"backends"_s] = backends;
        out << w;
    }
    m_workersView = out;
}

void ActivityEngine::setRestart(RestartPhase p)
{
    m_restartPhase = p;
    Q_EMIT restartChanged();
}

void ActivityEngine::askRestart()
{
    if (m_restartPhase == RestartIdle) {
        m_restartError.clear();
        m_restartResult.clear();
        setRestart(RestartConfirm);
    }
}

void ActivityEngine::dismissRestart()
{
    if (m_restartPhase == RestartRunning)
        return;
    m_restartError.clear();
    m_restartResult.clear();
    setRestart(RestartIdle);
}

void ActivityEngine::confirmRestart()
{
    Client *c = client();
    if (!c || m_restartPhase == RestartRunning)
        return;
    m_restartError.clear();
    setRestart(RestartRunning);
    // The same route as the web app (ActivityApp.aiStack.tsx:38): POST
    // /api/system/ai-stack/restart, no body. Admin only, enforced by the server.
    c->http(u"POST"_s, u"/api/system/ai-stack/restart"_s)->then(this, [this](const Result &r) {
        if (r.error.kind() == Error::Forbidden) {
            m_restartError = tr("Admin access is required to restart AI services.");
            setRestart(RestartError);
            return;
        }
        if (!r.ok() || !r.value.isObject()) {
            const Error &e = r.error;
            if (e.kind() == Error::Network)
                m_restartError = e.userMessage();
            else if (e.isError() && !e.message().isEmpty())
                m_restartError = e.message(); // the server's own `error` text
            else
                m_restartError = tr("The restart request failed.");
            setRestart(RestartError);
            return;
        }
        m_restartResult = r.value.toObject().toVariantMap();
        setRestart(RestartDone);
        refresh();
    });
}

} // namespace Taos::Activity
