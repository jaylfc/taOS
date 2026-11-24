// SPDX-License-Identifier: AGPL-3.0-or-later
#include "taosqt/livequery.h"

#include <QJsonArray>
#include <QTimer>

using namespace Qt::StringLiterals;

namespace Taos {

LiveQuery::LiveQuery(QObject *parent) : QObject(parent), m_model(new JsonListModel({}, u"id"_s, this)) {}

LiveQuery::LiveQuery(Client *client, const QString &op, const QJsonObject &args, const QStringList &eventKinds,
                     const QStringList &roles, QObject *parent, const QString &keyField, const QString &resultKey)
    : QObject(parent), m_client(client), m_op(op), m_args(args), m_kinds(eventKinds), m_roles(roles),
      m_keyField(keyField), m_resultKey(resultKey), m_model(new JsonListModel(roles, keyField, this))
{
}

LiveQuery::~LiveQuery()
{
    detach(); // releases the shared stream; the Client closes it with the last user
}

Client *LiveQuery::client() const
{
    return m_client ? m_client.data() : defaultClient();
}

void LiveQuery::setClient(Client *c)
{
    const bool was = m_attached;
    detach();
    m_client = c;
    if (was)
        applyRunning();
}

void LiveQuery::setOp(const QString &op) { m_op = op; Q_EMIT configChanged(); }
void LiveQuery::setArgs(const QVariantMap &a) { m_args = QJsonObject::fromVariantMap(a); Q_EMIT configChanged(); }
void LiveQuery::setEventKinds(const QStringList &k) { m_kinds = k; Q_EMIT configChanged(); }
void LiveQuery::setRoles(const QStringList &r) { m_roles = r; m_model->setRoles(r); Q_EMIT configChanged(); }
void LiveQuery::setKeyField(const QString &k) { m_keyField = k; m_model->setKeyField(k); Q_EMIT configChanged(); }

void LiveQuery::setRunning(bool on)
{
    if (m_wantRunning == on)
        return;
    m_wantRunning = on;
    Q_EMIT runningChanged();
    // Deferred so that every property of a QML declaration is set first.
    QTimer::singleShot(0, this, &LiveQuery::applyRunning);
}

void LiveQuery::applyRunning()
{
    if (m_wantRunning && !m_attached) {
        if (Client *c = client())
            attach(c);
    } else if (!m_wantRunning && m_attached) {
        detach();
    }
}

void LiveQuery::attach(Client *c)
{
    m_client = c;
    m_attached = true;
    m_stream = c->sharedOsEvents();
    connect(m_stream, &EventStream::eventReceived, this, [this](const QString &, const QString &, const QJsonValue &d) {
        const QString kind = d.toObject().value(u"kind"_s).toString();
        if (m_kinds.isEmpty() || m_kinds.contains(kind) || kind == u"events.lagged"_s)
            refresh();
    });
    // Every (re)connection: events sent while we were not connected are gone,
    // so fetch again. This also covers the gap between the first fetch below
    // and the stream going live.
    connect(m_stream, &EventStream::connected, this, &LiveQuery::refresh);
    connect(m_stream, &EventStream::stateChanged, this,
            [this](EventStream::State s) { setLive(s == EventStream::Live); });
    connect(c, &Client::credentialsChanged, this, &LiveQuery::refresh);
    setLive(m_stream->state() == EventStream::Live);
    c->retainOsEvents();
    refresh();
}

void LiveQuery::detach()
{
    if (!m_attached)
        return;
    m_attached = false;
    if (m_stream)
        disconnect(m_stream, nullptr, this, nullptr);
    if (m_client) {
        disconnect(m_client, nullptr, this, nullptr);
        m_client->releaseOsEvents();
    }
    setLive(false);
}

void LiveQuery::setLoading(bool b)
{
    if (m_loading != b) {
        m_loading = b;
        Q_EMIT loadingChanged();
    }
}

void LiveQuery::setLive(bool b)
{
    if (m_live != b) {
        m_live = b;
        Q_EMIT liveChanged();
    }
}

void LiveQuery::refresh()
{
    Client *c = client();
    if (!c || !m_attached)
        return;
    if (m_loading) {
        m_pending = true;
        return;
    }
    setLoading(true);
    c->call(m_op, m_args)->then(this, [this](const Result &r) {
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
