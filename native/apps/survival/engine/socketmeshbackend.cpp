// SPDX-License-Identifier: AGPL-3.0-or-later
#include "socketmeshbackend.h"

#include <QJsonArray>
#include <QJsonObject>
#include <algorithm>

using namespace Qt::StringLiterals;

namespace Taos::Survival {

namespace {
QJsonObject obj(const QJsonValue &v) { return v.toObject(); }
}

struct FetchAcc
{
    QJsonValue radios, ids, texts, pos, last, idl;
    QString healthError;
    bool healthOk = true, ro = true, cr = false, cw = false, cc = false, canConnect = false, transport = false, refused = false;
    QString transportMsg, refusedMsg;
};

SocketMeshBackend::SocketMeshBackend(MeshClient *client, QObject *parent) : MeshBackend(parent), m_client(client)
{
    m_coalesce.setSingleShot(true);
    m_coalesce.setInterval(300);
    connect(&m_coalesce, &QTimer::timeout, this, &SocketMeshBackend::fetchAll);
    connect(m_client, &MeshClient::recordStored, this, [this](const QJsonObject &) { m_coalesce.start(); });
    connect(m_client, &MeshClient::stateChanged, this, [this] {
        if (m_client->state() == MeshClient::State::Connected) {
            m_state = u"connecting"_s;
            Q_EMIT stateChanged();
            fetchAll();
        } else {
            m_state = m_client->state() == MeshClient::State::Connecting ? u"connecting"_s : u"absent"_s;
            m_inflight = 0;
            Q_EMIT stateChanged();
        }
    });
}

QString SocketMeshBackend::unavailableReason() const
{
    if (m_state == QLatin1String("connected"))
        return {};
    if (m_state == QLatin1String("failed"))
        return m_error.isEmpty() ? tr("The mesh engine answered with an error.") : m_error;
    if (m_state == QLatin1String("connecting"))
        return tr("Connecting to the mesh engine.");
    return tr("The mesh engine is not running.");
}

QString SocketMeshBackend::threadOf(const QJsonObject &r)
{
    if (r.value(u"kind"_s).toString() != u"text"_s)
        return {};
    if (r.value(u"broadcast"_s).toBool())
        return u"ch:%1:%2"_s.arg(r.value(u"network"_s).toString()).arg(r.value(u"channel"_s).toInt(0));
    return r.value(r.value(u"dir"_s).toString() == u"out"_s ? u"to"_s : u"from"_s).toString();
}

MeshMessage SocketMeshBackend::messageFromRecord(const QJsonObject &r)
{
    MeshMessage m;
    m.id = r.value(u"id"_s).toString();
    m.threadId = threadOf(r);
    m.fromId = r.value(u"from"_s).toString();
    m.toId = r.value(u"to"_s).toString();
    m.text = r.value(u"text"_s).toString();
    m.timeMs = qint64(r.value(u"host_ts_ms"_s).toDouble());
    m.outgoing = r.value(u"dir"_s).toString() == u"out"_s;
    m.delivery = r.value(u"delivery"_s).toString();
    m.trust = r.value(u"trust"_s).toString();
    m.network = r.value(u"network"_s).toString();
    const QJsonObject s = obj(r.value(m.outgoing ? u"recipient"_s : u"sender"_s));
    // the engine stores who sent a frame; for our own sends, who the radio belongs to is the observer
    const QJsonObject sender = obj(r.value(u"sender"_s));
    m.senderIdentity = sender.value(u"identity"_s).toString();
    m.senderName = sender.value(u"name"_s).toString();
    Q_UNUSED(s);
    const QJsonObject p = obj(r.value(u"position"_s));
    if (p.contains(u"lat"_s) && p.contains(u"lon"_s)) {
        m.hasPosition = true;
        m.lat = p.value(u"lat"_s).toDouble();
        m.lon = p.value(u"lon"_s).toDouble();
        if (p.contains(u"time"_s))
            m.positionTimeMs = qint64(p.value(u"time"_s).toDouble() * 1000.0);
        m.positionIsLastKnown = p.value(u"source"_s).toString() == u"last_known"_s;
    }
    const QJsonObject sg = obj(r.value(u"signal"_s));
    if (sg.contains(u"rssi"_s) || sg.contains(u"snr"_s)) {
        m.hasSignal = true;
        m.rssi = sg.value(u"rssi"_s).toDouble();
        m.snr = sg.value(u"snr"_s).toDouble();
    }
    m.hops = sg.contains(u"hops"_s) ? sg.value(u"hops"_s).toInt(-1) : -1;
    return m;
}

QList<MeshMessage> SocketMeshBackend::messages(const QString &threadId) const
{
    if (threadId.isEmpty())
        return m_msgs;
    QList<MeshMessage> out;
    for (const MeshMessage &m : m_msgs)
        if (m.threadId == threadId)
            out.append(m);
    return out;
}

QList<MeshTrailPoint> SocketMeshBackend::trail(const QString &node) const { return m_trails.value(node); }

MeshDeviceConfig SocketMeshBackend::deviceConfig(const QString &radioId) const
{
    auto it = m_cfg.find(radioId);
    if (it != m_cfg.end())
        return *it;
    MeshDeviceConfig c;
    c.radioId = radioId;
    if (m_state == QLatin1String("connected") && !m_cfgRead) {
        c.state = u"unsupported"_s;
        c.note = tr("This engine does not read radio settings yet. Nothing is shown rather than a guess.");
    } else {
        c.state = u"loading"_s;
    }
    return c;
}

void SocketMeshBackend::refresh() { fetchAll(); }

void SocketMeshBackend::fetchAll()
{
    if (m_client->state() != MeshClient::State::Connected)
        return;
    if (m_inflight > 0) {
        m_again = true;   // one more pass once this one lands
        return;
    }
    const int gen = ++m_gen;
    // health first: it says whether the engine is well and what it can do; the lists follow
    m_inflight = 7;
    auto acc = std::make_shared<FetchAcc>();
    auto landed = [this, gen, acc] {
        if (gen != m_gen)
            return;
        if (--m_inflight == 0) {
            apply(acc);
            recompute();
            if (m_again) {
                m_again = false;
                fetchAll();
            }
        }
    };
    auto store = [this, acc, gen](const MeshReply &r) {
        if (gen != m_gen)
            return;
        if (r.code == u"transport"_s) {
            acc->transport = true;
            acc->transportMsg = r.message;
        } else if (!r.ok && !acc->refused) {
            // the engine answered and said no: that is not "nothing there", it is a problem to show
            acc->refused = true;
            acc->refusedMsg = r.message;
        }
    };
    m_client->request(u"health"_s, {}, this, [this, acc, gen, landed](const MeshReply &r) {
        if (gen == m_gen && r.ok) {
            const QJsonObject h = r.result.toObject();
            acc->ro = h.value(u"receive_only"_s).toBool(true);   // unknown means receive-only: never assume sending is allowed
            acc->cr = h.value(u"device_config"_s).toBool(false);
            acc->cw = h.value(u"device_config_write"_s).toBool(false);
            acc->canConnect = h.value(u"radios_connect"_s).toBool(false);
            if (!h.value(u"store_error"_s).isNull() && !h.value(u"store_error"_s).toString().isEmpty()) {
                acc->healthOk = false;
                acc->healthError = tr("The mesh engine cannot write its store: %1").arg(h.value(u"store_error"_s).toString());
            }
        } else if (gen == m_gen) {
            acc->healthOk = false;
            acc->healthError = r.message;
        }
        // all later replies fill `acc`; the last one to land applies it
        landed();
    });
    auto fill = [this, acc, landed, store](const QString &method, const QJsonObject &params, QJsonValue FetchAcc::*member) {
        m_client->request(method, params, this, [acc, member, landed, store](const MeshReply &r) {
            store(r);
            if (r.ok)
                (*acc).*member = r.result;
            landed();
        });
    };
    fill(u"app.radios"_s, {}, &FetchAcc::radios);
    fill(u"app.identities"_s, {}, &FetchAcc::ids);
    fill(u"messages"_s, QJsonObject{{u"filter"_s, QJsonObject{{u"kind"_s, u"text"_s}, {u"limit"_s, 2000}, {u"newest_first"_s, true}}}}, &FetchAcc::texts);
    fill(u"messages"_s, QJsonObject{{u"filter"_s, QJsonObject{{u"kind"_s, u"position"_s}, {u"limit"_s, 4000}, {u"newest_first"_s, true}}}}, &FetchAcc::pos);
    fill(u"last_positions"_s, {}, &FetchAcc::last);
    // the 7th reply: identities.list; its landing applies everything
    m_client->request(u"identities.list"_s, {}, this, [acc, gen, this, landed](const MeshReply &r) {
        if (gen != m_gen)
            return;
        if (r.ok)
            acc->idl = r.result;
        landed();
    });
}

void SocketMeshBackend::apply(const std::shared_ptr<FetchAcc> &accp)
{
    const FetchAcc *acc = accp.get();
                m_receiveOnly = acc->ro;
        m_cfgRead = acc->cr;
        m_cfgWrite = acc->cw;
        m_canConnect = acc->canConnect;
        // parse into members
        m_radios.clear();
        for (const QJsonValue &v : acc->radios.toArray()) {
            const QJsonObject o = v.toObject();
            MeshRadio rd;
            rd.id = o.value(u"id"_s).toString();
            rd.name = o.value(u"name"_s).toString();
            rd.transport = o.value(u"transport"_s).toString();
            rd.link = o.value(u"link"_s).toString();
            rd.state = o.value(u"state"_s).toString();
            rd.firmware = o.value(u"firmware"_s).toString();
            rd.batteryPercent = o.value(u"batteryPercent"_s).toInt(-1);
            m_radios.append(rd);
        }
        m_ids.clear();
        for (const QJsonValue &v : acc->ids.toArray()) {
            const QJsonObject o = v.toObject();
            MeshIdentity i;
            i.id = o.value(u"id"_s).toString();
            i.callsign = o.value(u"callsign"_s).toString();
            i.owner = o.value(u"owner"_s).toString();
            i.isSelf = o.value(u"isSelf"_s).toBool();
            i.hasPosition = o.value(u"hasPosition"_s).toBool();
            i.lon = o.value(u"lon"_s).toDouble();
            i.lat = o.value(u"lat"_s).toDouble();
            i.lastHeardMs = qint64(o.value(u"lastHeardMs"_s).toDouble());
            m_ids.append(i);
        }
        m_msgs.clear();
        for (const QJsonValue &v : acc->texts.toArray()) {
            const QJsonObject o = v.toObject();
            const QString d = o.value(u"dir"_s).toString();
            if (d != u"in"_s && d != u"out"_s)
                continue;
            MeshMessage m = messageFromRecord(o);
            if (!m.threadId.isEmpty())
                m_msgs.append(m);
        }
        std::sort(m_msgs.begin(), m_msgs.end(), [](const MeshMessage &a, const MeshMessage &b) { return a.timeMs < b.timeMs; });
        m_trails.clear();
        for (const QJsonValue &v : acc->pos.toArray()) {
            const QJsonObject o = v.toObject();
            const QJsonObject p = o.value(u"position"_s).toObject();
            if (o.value(u"dir"_s).toString() != u"in"_s || p.value(u"source"_s).toString() == u"last_known"_s || !p.contains(u"lat"_s))
                continue;
            MeshTrailPoint t;
            t.lat = p.value(u"lat"_s).toDouble();
            t.lon = p.value(u"lon"_s).toDouble();
            t.timeMs = p.contains(u"time"_s) ? qint64(p.value(u"time"_s).toDouble() * 1000.0) : qint64(o.value(u"host_ts_ms"_s).toDouble());
            m_trails[o.value(u"from"_s).toString()].append(t);
        }
        for (auto it = m_trails.begin(); it != m_trails.end(); ++it)
            std::sort(it->begin(), it->end(), [](const MeshTrailPoint &a, const MeshTrailPoint &b) { return a.timeMs < b.timeMs; });
        m_places.clear();
        for (const QJsonValue &v : acc->last.toArray()) {
            const QJsonObject o = v.toObject();
            const QJsonObject p = o.value(u"position"_s).toObject();
            MeshPlace pl;
            pl.node = o.value(u"node"_s).toString();
            pl.identity = o.value(u"identity"_s).toString();
            pl.name = o.value(u"name"_s).toString();
            pl.network = o.value(u"network"_s).toString();
            pl.lat = p.value(u"lat"_s).toDouble();
            pl.lon = p.value(u"lon"_s).toDouble();
            pl.heardMs = qint64(o.value(u"heard_ms"_s).toDouble());
            pl.positionTimeMs = p.contains(u"time"_s) ? qint64(p.value(u"time"_s).toDouble() * 1000.0) : pl.heardMs;
            m_places.append(pl);
        }
        m_contacts.clear();
        m_assign.clear();
        const QJsonObject idl = acc->idl.toObject();
        for (const QJsonValue &v : idl.value(u"contacts"_s).toArray()) {
            const QJsonObject o = v.toObject();
            MeshContact c;
            c.id = o.value(u"id"_s).toString();
            c.name = o.value(u"name"_s).toString();
            c.notes = o.value(u"notes"_s).toString();
            c.linkedAccount = o.value(u"linked_account"_s).toString();
            c.told = o.value(u"positions_told"_s).toBool();
            c.toldDate = o.value(u"positions_told_date"_s).toString();
            c.locationsHidden = !o.value(u"locations_hidden_ts"_s).isNull();
            m_contacts.append(c);
        }
        for (const QJsonValue &v : idl.value(u"assignments"_s).toArray()) {
            const QJsonObject o = v.toObject();
            m_assign.append({o.value(u"node"_s).toString(), o.value(u"identity"_s).toString(), o.value(u"name"_s).toString()});
        }
        if (!acc->healthOk) {
            m_state = u"failed"_s;
            m_error = acc->healthError;
        } else if (acc->transport) {
            m_state = u"failed"_s;
            m_error = acc->transportMsg;
        } else if (acc->refused) {
            m_state = u"failed"_s;
            m_error = tr("The mesh engine refused a request: %1").arg(acc->refusedMsg);
        } else {
            m_state = u"connected"_s;
            m_error.clear();
        }
        if (m_state == QLatin1String("connected") && !m_cfgWanted.isEmpty()) {
            const auto wanted = std::exchange(m_cfgWanted, {});
            for (const QString &r : wanted)
                QMetaObject::invokeMethod(this, [this, r] { requestDeviceConfig(r); }, Qt::QueuedConnection);
        }
}

void SocketMeshBackend::recompute()
{
    Q_EMIT stateChanged();
    Q_EMIT radiosChanged();
    Q_EMIT identitiesChanged();
    Q_EMIT messagesChanged();
}

MeshSendResult SocketMeshBackend::send(const QString &threadId, const QString &text)
{
    if (!available())
        return {false, QString(), unavailableReason()};
    if (m_receiveOnly)
        return {false, QString(), tr("The mesh engine is receive-only, so nothing can be sent.")};
    m_client->request(u"app.send"_s, QJsonObject{{u"thread"_s, threadId}, {u"text"_s, text}}, this, [this](const MeshReply &r) {
        if (!r.ok) {
            Q_EMIT actionDone(u"send"_s, false, r.message);
            return;
        }
        const QJsonObject o = r.result.toObject();
        if (!o.value(u"accepted"_s).toBool()) {
            Q_EMIT actionDone(u"send"_s, false, o.value(u"error"_s).toString().isEmpty() ? tr("The engine did not accept the message.") : o.value(u"error"_s).toString());
            return;
        }
        Q_EMIT actionDone(u"send"_s, true, QString());
        m_coalesce.start();
    });
    // handed to the engine; its answer arrives as actionDone
    return {true, QString(), QString()};
}

void SocketMeshBackend::done(const QString &what, const MeshReply &r)
{
    Q_EMIT actionDone(what, r.ok, r.ok ? QString() : r.message);
    if (r.ok)
        fetchAll();
}

QString SocketMeshBackend::explainConnectError(const QString &m)
{
    const QString low = m.toLower();
    if (low.contains(u"permission denied"_s) || low.contains(u"os error 13"_s)) {
        const QString user = QString::fromLocal8Bit(qgetenv("USER"));
        return QObject::tr("This account is not allowed to open serial ports. Add it to the dialout group once with: sudo usermod -aG dialout %1 "
                           "and then sign out and back in. There is no privileged helper in taOS for this yet, so it cannot be done from here.")
            .arg(user.isEmpty() ? QObject::tr("YOUR-USER") : user);
    }
    if (low.contains(u"busy"_s) || low.contains(u"os error 16"_s) || low.contains(u"in use"_s))
        return QObject::tr("Another program is using this port (a serial monitor, ModemManager or another engine). Close it and try again.");
    return m;
}

void SocketMeshBackend::discoverDevices()
{
    if (m_client->state() != MeshClient::State::Connected)
        return;
    m_client->request(u"radios.saved"_s, {}, this, [this](const MeshReply &s) {
        if (s.ok) {
            m_saved.clear();
            for (const QJsonValue &v : s.result.toArray())
                m_saved << v.toObject().value(u"device"_s).toString();
        }
        m_client->request(u"discover"_s, {}, this, [this](const MeshReply &r) {
            if (!r.ok)
                return;
            QList<MeshDevice> out;
            const QJsonObject o = r.result.toObject();
            for (const QJsonValue &v : o.value(u"serial"_s).toArray()) {
                const QJsonObject d = v.toObject();
                MeshDevice m;
                m.id = d.value(u"path"_s).toString();
                m.kind = u"usb"_s;
                const QString prod = d.value(u"product"_s).toString(), man = d.value(u"manufacturer"_s).toString();
                m.label = !prod.isEmpty() ? prod : !man.isEmpty() ? man : tr("USB serial device");
                m.detail = u"%1  %2:%3"_s.arg(m.id, QString::number(d.value(u"vid"_s).toInt(), 16).rightJustified(4, QLatin1Char('0')), QString::number(d.value(u"pid"_s).toInt(), 16).rightJustified(4, QLatin1Char('0')));
                m.hint = d.value(u"hint"_s).toString();
                m.saved = m_saved.contains(m.id);
                out << m;
            }
            const QJsonObject ble = o.value(u"ble"_s).toObject();
            for (const QJsonValue &v : ble.value(u"devices"_s).toArray()) {
                const QJsonObject d = v.toObject();
                MeshDevice m;
                m.id = u"ble:"_s + d.value(u"address"_s).toString();
                m.kind = u"ble"_s;
                m.label = d.value(u"name"_s).toString().isEmpty() ? tr("Bluetooth radio") : d.value(u"name"_s).toString();
                m.detail = d.value(u"address"_s).toString();
                m.hint = d.value(u"network"_s).toString();
                m.supported = false;   // the engine has no Bluetooth link yet
                out << m;
            }
            m_devices = out;
            Q_EMIT devicesChanged();
        });
    });
}

void SocketMeshBackend::connectDevice(const QString &id, const QString &network)
{
    if (!m_canConnect) {
        Q_EMIT actionDone(u"connect:"_s + id, false, tr("This engine cannot be told to open radios. Update it."));
        return;
    }
    QJsonObject p{{u"device"_s, id}};
    if (!network.isEmpty())
        p.insert(u"network"_s, network);
    m_client->request(u"radios.connect"_s, p, this, [this, id](const MeshReply &r) {
        Q_EMIT actionDone(u"connect:"_s + id, r.ok, r.ok ? QString() : explainConnectError(r.message));
        if (r.ok) {
            fetchAll();
            discoverDevices();
        }
    });
}

void SocketMeshBackend::forgetDevice(const QString &id)
{
    m_client->request(u"radios.disconnect"_s, QJsonObject{{u"device"_s, id}}, this, [this, id](const MeshReply &r) {
        Q_EMIT actionDone(u"forget:"_s + id, r.ok, r.message);
        if (r.ok)
            discoverDevices();
    });
}

void SocketMeshBackend::assign(const QString &node, const QString &identity, const QString &name)
{
    QJsonObject p{{u"node"_s, node}, {u"identity"_s, identity.isEmpty() ? QJsonValue() : QJsonValue(identity)}};
    if (!name.isEmpty())
        p.insert(u"name"_s, name);
    m_client->request(u"identities.assign"_s, p, this, [this](const MeshReply &r) { done(u"assign"_s, r); });
}

void SocketMeshBackend::createContact(const QString &name, const QString &notes, const QString &assignNode)
{
    m_client->request(u"contacts.create"_s, QJsonObject{{u"name"_s, name}, {u"notes"_s, notes}}, this, [this, assignNode](const MeshReply &r) {
        if (!r.ok || assignNode.isEmpty()) {
            done(u"contact"_s, r);
            return;
        }
        const QString cid = r.result.toObject().value(u"contact"_s).toString();
        m_client->request(u"identities.assign"_s, QJsonObject{{u"node"_s, assignNode}, {u"identity"_s, u"contact:"_s + cid}}, this,
                          [this](const MeshReply &r2) { done(u"contact"_s, r2); });
    });
}

void SocketMeshBackend::setTold(const QString &contact, bool told, const QString &date)
{
    QJsonObject p{{u"contact"_s, contact}, {u"told"_s, told}};
    if (!date.isEmpty())
        p.insert(u"date"_s, date);
    m_client->request(u"contacts.told"_s, p, this, [this](const MeshReply &r) { done(u"told"_s, r); });
}

void SocketMeshBackend::link(const QString &contact, const QString &account)
{
    m_client->request(u"link"_s, QJsonObject{{u"contact"_s, contact}, {u"account"_s, account}}, this, [this](const MeshReply &r) { done(u"link"_s, r); });
}

void SocketMeshBackend::requestDeviceConfig(const QString &radioId)
{
    if (m_state != QLatin1String("connected")) {
        m_cfgWanted.insert(radioId);   // asked again as soon as the engine has said what it can do
        Q_EMIT radiosChanged();
        return;
    }
    if (!m_cfgRead) {
        Q_EMIT radiosChanged();   // deviceConfig() says "unsupported"
        return;
    }
    MeshDeviceConfig c;
    c.radioId = radioId;
    c.state = u"loading"_s;
    m_cfg.insert(radioId, c);
    Q_EMIT radiosChanged();
    m_client->request(u"device.config"_s, QJsonObject{{u"radio"_s, radioId}}, this, [this, radioId](const MeshReply &r) {
        MeshDeviceConfig c;
        c.radioId = radioId;
        if (r.ok) {
            c.state = u"ok"_s;
            c.values = r.result.toObject().toVariantMap();
        } else if (r.code == u"unknown_method"_s) {
            c.state = u"unsupported"_s;
            c.note = tr("This engine does not read radio settings yet.");
        } else {
            c.state = u"error"_s;
            c.note = r.message;
        }
        m_cfg.insert(radioId, c);
        Q_EMIT radiosChanged();
    });
}

void SocketMeshBackend::applyDeviceConfig(const QString &radioId, const QVariantMap &changes)
{
    if (!m_cfgWrite) {
        Q_EMIT actionDone(u"device"_s, false, tr("The engine does not write radio settings yet, so nothing was changed."));
        return;
    }
    m_client->request(u"device.apply"_s, QJsonObject{{u"radio"_s, radioId}, {u"changes"_s, QJsonObject::fromVariantMap(changes)}}, this,
                      [this](const MeshReply &r) { done(u"device"_s, r); });
}

} // namespace Taos::Survival
