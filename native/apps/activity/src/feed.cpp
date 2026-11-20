#include "feed.h"

#include <QJsonArray>
#include <QJsonObject>
#include <QSet>

using namespace Qt::StringLiterals;

namespace Taos::Activity {

ModelActivityFeed::ModelActivityFeed(QObject *parent)
    : QObject(parent),
      m_model(new JsonListModel({u"seq"_s, u"ts"_s, u"event"_s, u"model"_s, u"worker"_s, u"backend"_s,
                                 u"duration_ms"_s, u"tokens_in"_s, u"tokens_out"_s, u"token_rate"_s, u"reason"_s},
                                u"seq"_s, this))
{
}
ModelActivityFeed::~ModelActivityFeed() = default;

Client *ModelActivityFeed::client() const { return m_client ? m_client.data() : defaultClient(); }

void ModelActivityFeed::setClient(Client *c)
{
    if (m_client == c)
        return;
    m_client = c;
    Q_EMIT clientChanged();
}

void ModelActivityFeed::setActive(bool on)
{
    if (m_active == on)
        return;
    m_active = on;
    if (on)
        restart(false); // history again (merged, nothing is dropped) and a fresh stream
    else
        closeStream();  // hidden: no stream, no polling
    Q_EMIT activeChanged();
}

void ModelActivityFeed::setWorker(const QString &v)
{
    if (m_worker == v) return;
    m_worker = v;
    Q_EMIT filtersChanged();
    if (m_active) restart(true);
}
void ModelActivityFeed::setModelFilter(const QString &v)
{
    if (m_modelFilter == v) return;
    m_modelFilter = v;
    Q_EMIT filtersChanged();
    if (m_active) restart(true);
}
void ModelActivityFeed::setEventType(const QString &v)
{
    if (m_event == v) return;
    m_event = v;
    Q_EMIT filtersChanged();
    if (m_active) restart(true);
}

void ModelActivityFeed::reload()
{
    if (m_active)
        restart(true);
}

QJsonObject ModelActivityFeed::query(bool stream) const
{
    QJsonObject q;
    if (!m_worker.isEmpty()) q.insert(u"worker"_s, m_worker);
    if (!m_modelFilter.isEmpty()) q.insert(u"model"_s, m_modelFilter);
    if (!m_event.isEmpty()) q.insert(u"event"_s, m_event);
    // History asks for the 200 the panel keeps; the stream replays nothing
    // (the history already delivered that window): the web app's STREAM_REPLAY = 0.
    q.insert(u"limit"_s, stream ? 0 : kMaxEvents);
    return q;
}

void ModelActivityFeed::setLive(bool on)
{
    if (m_live == on) return;
    m_live = on;
    Q_EMIT liveChanged();
}

void ModelActivityFeed::setError(const QString &text, bool signIn)
{
    if (m_error == text && m_signIn == signIn) return;
    m_error = text;
    m_signIn = signIn;
    Q_EMIT errorChanged();
}

void ModelActivityFeed::closeStream()
{
    ++m_generation;
    if (m_history) m_history->deleteLater(); // cancels in the transport
    if (m_stream) { m_stream->stop(); m_stream->deleteLater(); }
    m_history = nullptr;
    m_stream = nullptr;
    setLive(false);
    if (m_loading) { m_loading = false; Q_EMIT loadingChanged(); }
}

void ModelActivityFeed::restart(bool clear)
{
    Client *c = client();
    if (!c)
        return;
    closeStream();
    // A filter change or a manual reload starts from an empty list (the web
    // app's effect does the same). Re-showing a hidden window keeps the rows
    // and merges the fresh history into them.
    if (clear) {
        m_bySeq.clear();
        publish();
    }
    m_loading = true;
    Q_EMIT loadingChanged();
    setError(QString(), false);
    const quint64 gen = m_generation;
    m_history = c->http(u"GET"_s, u"/api/activity/models"_s, query(false));
    m_history->then(this, [this, gen](const Result &r) {
        if (gen != m_generation)
            return;
        m_loading = false;
        Q_EMIT loadingChanged();
        if (!r.ok()) {
            const bool auth = r.error.kind() == Error::Unauthenticated;
            setError(auth ? tr("Sign in to see model activity") : r.error.userMessage(), auth);
            return;
        }
        const QJsonObject o = r.value.toObject();
        absorb(o.value(u"events"_s).toArray());
        const QJsonArray types = o.value(u"event_types"_s).toArray();
        if (!types.isEmpty()) {
            m_types.clear();
            for (const QJsonValue &t : types) m_types << t.toString();
            Q_EMIT optionsChanged();
        }
    });
    openStream();
}

void ModelActivityFeed::openStream()
{
    Client *c = client();
    if (!c) return;
    m_stream = c->openStream(u"/api/activity/models/stream"_s, query(true), this);
    const quint64 gen = m_generation;
    connect(m_stream, &EventStream::stateChanged, this, [this, gen](EventStream::State s) {
        if (gen != m_generation) return;
        setLive(s == EventStream::Live);
        if (s == EventStream::AuthRequired)
            setError(tr("Sign in to see model activity"), true);
    });
    connect(m_stream, &EventStream::eventReceived, this, [this, gen](const QString &, const QString &, const QJsonValue &data) {
        if (gen != m_generation || !data.isObject()) return;
        if (!data.toObject().contains(u"seq"_s)) return;
        absorb(QJsonArray{data});
    });
    m_stream->start();
}

void ModelActivityFeed::absorb(const QJsonArray &events)
{
    bool optionsGrew = false;
    bool any = false;
    for (const QJsonValue &v : events) {
        const QJsonObject e = v.toObject();
        if (!e.value(u"seq"_s).isDouble())
            continue;
        any = true;
        m_bySeq.insert(qint64(e.value(u"seq"_s).toDouble()), e); // merge by seq: the same seq replaces
        const QString w = e.value(u"worker"_s).toString();
        const QString m = e.value(u"model"_s).toString();
        if (!w.isEmpty() && !m_workers.contains(w)) { m_workers.insert(w); optionsGrew = true; }
        if (!m.isEmpty() && !m_models.contains(m)) { m_models.insert(m); optionsGrew = true; }
    }
    if (!any)
        return;
    while (m_bySeq.size() > kMaxEvents)
        m_bySeq.erase(m_bySeq.begin()); // drop the oldest
    publish();
    if (optionsGrew)
        Q_EMIT optionsChanged();
}

void ModelActivityFeed::publish()
{
    QJsonArray rows;
    for (auto it = m_bySeq.constEnd(); it != m_bySeq.constBegin();) {
        --it;
        rows.append(it.value());
    }
    m_model->setRows(rows);
    Q_EMIT eventsChanged();
}

} // namespace Taos::Activity
