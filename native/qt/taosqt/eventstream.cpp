#include "taosqt/eventstream.h"

#include <QJsonDocument>

#include "taos_client.h"

using namespace Qt::StringLiterals;

namespace Taos {

EventStream::EventStream(Client *client, QString path, QJsonObject query, QObject *parent)
    : QObject(parent), m_client(client), m_path(std::move(path)), m_query(std::move(query))
{
    client->registerStream(this);
}

EventStream::~EventStream()
{
    if (m_client)
        m_client->unregisterStream(this); // cancels the connection in the transport
}

void EventStream::start()
{
    if (!m_client || m_state == Connecting || m_state == Live || m_state == Reconnecting)
        return;
    m_id = m_client->startStream(this, m_path, m_query);
    setState(m_id ? Connecting : Ended);
}

void EventStream::stop()
{
    if (m_client && m_id)
        m_client->cancelId(m_id);
    m_id = 0;
    setState(Idle);
}

void EventStream::restartAfterCredentials()
{
    if (m_state == AuthRequired)
        start();
}

void EventStream::reopen()
{
    // The transport was replaced: the old id is dead.
    m_id = 0;
    if (m_state == Connecting || m_state == Live || m_state == Reconnecting || m_state == AuthRequired) {
        m_state = Idle;
        start();
    }
}

void EventStream::setState(State s)
{
    if (m_state == s)
        return;
    m_state = s;
    emit stateChanged(s);
}

void EventStream::handle(int kind, const QByteArray &json)
{
    const QJsonObject o = QJsonDocument::fromJson(json).object();
    switch (kind) {
    case TAOS_EV_STREAM_EVENT: {
        setState(Live);
        const QJsonValue data = o.value(u"data"_s);
        if (!m_deliverHeartbeats && data.isObject()
            && data.toObject().value(u"kind"_s).toString() == u"events.heartbeat"_s)
            return;
        emit eventReceived(o.value(u"event"_s).toString(), o.value(u"id"_s).toString(), data);
        break;
    }
    case TAOS_EV_STREAM_CONNECTED:
        setState(Live);
        emit connected();
        break;
    case TAOS_EV_STREAM_RETRYING:
        m_lastError = Error::fromJson(o);
        setState(Reconnecting);
        emit errorOccurred(m_lastError);
        break;
    case TAOS_EV_STREAM_ENDED:
        m_id = 0;
        m_lastError = Error::fromJson(o);
        if (m_lastError.kind() == Error::Unauthenticated) {
            setState(AuthRequired);
            m_client->noteResult(m_lastError, QStringLiteral("stream")); // the coalesced sign-in signal
        } else {
            setState(Ended);
        }
        emit errorOccurred(m_lastError);
        break;
    default:
        break;
    }
}

} // namespace Taos
