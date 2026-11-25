// SPDX-License-Identifier: AGPL-3.0-or-later
#include "meshclient.h"

#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>

using namespace Qt::StringLiterals;

namespace Taos::Survival {

MeshClient::MeshClient(QString socketPath, QObject *parent) : QObject(parent), m_path(std::move(socketPath))
{
    m_retry.setSingleShot(true);
    connect(&m_retry, &QTimer::timeout, this, &MeshClient::tryConnect);
    connect(&m_cmd, &QLocalSocket::connected, this, [this] {
        m_backoffMs = 1000;
        setState(State::Connected);
        openStream();
    });
    connect(&m_cmd, &QLocalSocket::readyRead, this, &MeshClient::onReadyRead);
    connect(&m_cmd, &QLocalSocket::disconnected, this, &MeshClient::onCommandDown);
    connect(&m_cmd, &QLocalSocket::errorOccurred, this, [this](QLocalSocket::LocalSocketError) {
        if (m_cmd.state() != QLocalSocket::ConnectedState)
            onCommandDown();
    });
    connect(&m_stream, &QLocalSocket::connected, this, [this] {
        // the stream connection only ever sends `subscribe`
        m_stream.write("{\"id\":0,\"method\":\"subscribe\",\"principal\":\"owner\",\"params\":{}}\n");
    });
    connect(&m_stream, &QLocalSocket::readyRead, this, &MeshClient::onStreamRead);
}

MeshClient::~MeshClient()
{
    m_running = false;
    m_cmd.disconnect(this);
    m_stream.disconnect(this);
    m_cmd.abort();
    m_stream.abort();
}

QString MeshClient::defaultSocketPath()
{
    const QByteArray e = qgetenv("TAOS_MESH_SOCKET");
    if (!e.isEmpty())
        return QString::fromLocal8Bit(e);
    const QByteArray rt = qgetenv("XDG_RUNTIME_DIR");
    return (rt.isEmpty() ? QDir::tempPath() : QString::fromLocal8Bit(rt)) + u"/taos-mesh.sock"_s;
}

void MeshClient::setState(State s)
{
    if (s == m_state)
        return;
    m_state = s;
    Q_EMIT stateChanged();
}

void MeshClient::start()
{
    m_running = true;
    if (m_state == State::Absent && !m_retry.isActive())
        tryConnect();
}

void MeshClient::stop()
{
    m_running = false;
    m_retry.stop();
    m_cmd.abort();
    m_stream.abort();
    failAll(tr("The connection to the mesh engine was closed."));
    setState(State::Absent);
}

void MeshClient::tryConnect()
{
    if (!m_running)
        return;
    // no socket file: the engine is not running; look again later without making noise
    if (!QFileInfo::exists(m_path)) {
        setState(State::Absent);
        scheduleRetry();
        return;
    }
    setState(State::Connecting);
    m_cmdBuf.clear();
    m_cmd.connectToServer(m_path);
}

void MeshClient::scheduleRetry()
{
    if (!m_running)
        return;
    m_retry.start(m_backoffMs);
    m_backoffMs = qMin(m_backoffMs * 2, 15000);
}

void MeshClient::onCommandDown()
{
    m_stream.abort();
    failAll(tr("The connection to the mesh engine was lost."));
    setState(State::Absent);
    scheduleRetry();
}

void MeshClient::failAll(const QString &why)
{
    const auto all = std::exchange(m_pending, {});
    for (auto it = all.begin(); it != all.end(); ++it) {
        if (it->timeout) {
            it->timeout->stop();
            it->timeout->deleteLater();
        }
        if (it->ctx && it->cb) {
            MeshReply r;
            r.code = u"transport"_s;
            r.message = why;
            it->cb(r);
        }
    }
}

void MeshClient::request(const QString &method, const QJsonObject &params, QObject *ctx, Callback cb)
{
    if (m_state != State::Connected || m_cmd.state() != QLocalSocket::ConnectedState) {
        MeshReply r;
        r.code = u"transport"_s;
        r.message = tr("The mesh engine is not running.");
        if (cb)
            cb(r);
        return;
    }
    const qint64 id = m_nextId++;
    Pending p;
    p.ctx = ctx;
    p.cb = std::move(cb);
    p.timeout = new QTimer(this);
    p.timeout->setSingleShot(true);
    connect(p.timeout, &QTimer::timeout, this, [this, id] {
        auto it = m_pending.find(id);
        if (it == m_pending.end())
            return;
        Pending q = *it;
        m_pending.erase(it);
        q.timeout->deleteLater();
        MeshReply r;
        r.code = u"transport"_s;
        r.message = tr("The mesh engine did not answer in time.");
        if (q.ctx && q.cb)
            q.cb(r);
    });
    p.timeout->start(10000);
    m_pending.insert(id, p);
    const QJsonObject req{{u"id"_s, id}, {u"method"_s, method}, {u"principal"_s, u"owner"_s}, {u"params"_s, params}};
    m_cmd.write(QJsonDocument(req).toJson(QJsonDocument::Compact) + '\n');
}

void MeshClient::onReadyRead()
{
    m_cmdBuf += m_cmd.readAll();
    int nl;
    while ((nl = m_cmdBuf.indexOf('\n')) >= 0) {
        const QByteArray line = m_cmdBuf.left(nl);
        m_cmdBuf.remove(0, nl + 1);
        QJsonParseError pe;
        const QJsonDocument d = QJsonDocument::fromJson(line, &pe);
        if (pe.error != QJsonParseError::NoError || !d.isObject())
            continue;
        const QJsonObject o = d.object();
        const qint64 id = qint64(o.value(u"id"_s).toDouble(-1));
        auto it = m_pending.find(id);
        if (it == m_pending.end())
            continue;
        Pending p = *it;
        m_pending.erase(it);
        if (p.timeout) {
            p.timeout->stop();
            p.timeout->deleteLater();
        }
        MeshReply r;
        r.ok = o.value(u"ok"_s).toBool();
        r.result = o.value(u"result"_s);
        if (!r.ok) {
            const QJsonObject e = o.value(u"error"_s).toObject();
            r.code = e.value(u"code"_s).toString();
            r.message = e.value(u"message"_s).toString();
            if (r.message.isEmpty())
                r.message = tr("The mesh engine refused the request.");
        }
        if (p.ctx && p.cb)
            p.cb(r);
    }
}

void MeshClient::openStream()
{
    m_streamBuf.clear();
    m_stream.connectToServer(m_path);
}

void MeshClient::onStreamRead()
{
    m_streamBuf += m_stream.readAll();
    int nl;
    while ((nl = m_streamBuf.indexOf('\n')) >= 0) {
        const QByteArray line = m_streamBuf.left(nl);
        m_streamBuf.remove(0, nl + 1);
        const QJsonDocument d = QJsonDocument::fromJson(line);
        if (d.isObject() && d.object().value(u"event"_s).toString() == u"record"_s)
            Q_EMIT recordStored(d.object().value(u"record"_s).toObject());
    }
}

} // namespace Taos::Survival
