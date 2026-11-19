#include "taosqt/livequery.h"

#include <QJsonArray>

using namespace Qt::StringLiterals;

namespace Taos {

LiveQuery::LiveQuery(Client *client, const QString &op, const QJsonObject &args, const QStringList &eventKinds,
                     const QStringList &roles, QObject *parent, const QString &keyField, const QString &resultKey)
    : QObject(parent), m_client(client), m_op(op), m_args(args), m_kinds(eventKinds), m_resultKey(resultKey),
      m_model(new JsonListModel(roles, keyField, this))
{
    m_stream = client->osEvents({}, this);
    connect(m_stream, &EventStream::eventReceived, this, [this](const QString &, const QString &, const QJsonValue &d) {
        const QString kind = d.toObject().value(u"kind"_s).toString();
        if (m_kinds.isEmpty() || m_kinds.contains(kind) || kind == u"events.lagged"_s)
            refresh();
    });
    connect(m_stream, &EventStream::stateChanged, this, &LiveQuery::onStreamState);
    connect(client, &Client::credentialsChanged, this, [this] {
        if (m_started)
            refresh();
    });
}

LiveQuery::~LiveQuery() = default; // the stream is a child: its destructor closes the connection

void LiveQuery::start()
{
    if (m_started)
        return;
    m_started = true;
    m_stream->start();
    refresh();
}

void LiveQuery::stop()
{
    m_started = false;
    m_stream->stop();
}

void LiveQuery::setLoading(bool b)
{
    if (m_loading != b) {
        m_loading = b;
        Q_EMIT loadingChanged();
    }
}

void LiveQuery::onStreamState(EventStream::State s)
{
    const bool nowLive = (s == EventStream::Live);
    if (nowLive != m_live) {
        m_live = nowLive;
        Q_EMIT liveChanged();
    }
    // Catch up after a gap (the first Live is covered by start()'s fetch).
    if (s == EventStream::Reconnecting || s == EventStream::AuthRequired)
        m_hadLive = true; // reused as "a gap happened"
    else if (nowLive && m_hadLive) {
        m_hadLive = false;
        refresh();
    }
}

void LiveQuery::refresh()
{
    if (m_loading) {
        m_pending = true;
        return;
    }
    setLoading(true);
    m_client->call(m_op, m_args)->then(this, [this](const Result &r) {
        setLoading(false);
        if (r.ok()) {
            QJsonValue v = r.value;
            if (!m_resultKey.isEmpty())
                v = v.toObject().value(m_resultKey);
            m_model->setRows(v.toArray());
            if (!m_errorText.isEmpty()) {
                m_errorText.clear();
                Q_EMIT errorChanged();
            }
            Q_EMIT loaded();
        } else if (r.error.kind() != Error::Cancelled) {
            m_errorText = r.error.userMessage();
            Q_EMIT errorChanged();
            Q_EMIT failed(r.error);
        }
        if (m_pending) {
            m_pending = false;
            refresh();
        }
    });
}

} // namespace Taos
