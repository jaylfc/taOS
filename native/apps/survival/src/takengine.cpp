// SPDX-License-Identifier: AGPL-3.0-or-later
#include "takengine.h"

#include <QDate>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QLocale>
#include <QProcess>
#include <algorithm>
#include <cmath>

#include "meshformat.h"
#include "qrcode.h"

using namespace Qt::StringLiterals;

namespace Taos::Survival {

namespace {
QString coords(double lon, double lat)
{
    return u"%1 %2, %3 %4"_s.arg(std::abs(lat), 0, 'f', 4).arg(lat >= 0 ? u"N"_s : u"S"_s).arg(std::abs(lon), 0, 'f', 4).arg(lon >= 0 ? u"E"_s : u"W"_s);
}
QString networkName(const QString &n)
{
    return n == u"meshtastic"_s ? QStringLiteral("Meshtastic") : n == u"meshcore"_s ? QStringLiteral("MeshCore") : n == u"reticulum"_s ? QStringLiteral("Reticulum") : n;
}
} // namespace

TakEngine::TakEngine(MeshBackend *backend, const QString &dataDir, QObject *parent) : QObject(parent), m_b(backend), m_prefs(dataDir), m_profiles(dataDir)
{
    if (!m_prefs.load())
        setNotice(tr("The saved mesh settings could not be read, so sending stays off."), true);
    m_noticeTimer.setSingleShot(true);
    m_noticeTimer.setInterval(8000);
    connect(&m_noticeTimer, &QTimer::timeout, this, &TakEngine::clearNotice);
    setBackend(backend);
}

void TakEngine::setBackend(MeshBackend *b)
{
    if (m_b)
        m_b->disconnect(this);
    m_b = b;
    for (auto sig : {&MeshBackend::radiosChanged, &MeshBackend::identitiesChanged, &MeshBackend::messagesChanged, &MeshBackend::stateChanged, &MeshBackend::devicesChanged})
        connect(m_b, sig, this, &TakEngine::changed);
    connect(m_b, &MeshBackend::actionDone, this, [this](const QString &what, bool ok, const QString &err) {
        if (what.startsWith(u"connect:"_s)) {
            const QString id = what.mid(8);
            if (m_connecting == id)
                m_connecting.clear();
            if (ok) {
                m_connectErrors.remove(id);
                setNotice(tr("Connected. The radio is read only until you turn sending on."), false);
            } else {
                m_connectErrors.insert(id, err);   // stays until the next attempt: the fix may need a sign-out
            }
            Q_EMIT changed();
            return;
        }
        if (what.startsWith(u"forget:"_s)) {
            setNotice(ok ? tr("Forgotten. The engine stops opening it after its next restart.") : err, !ok);
            Q_EMIT changed();
            return;
        }
        if (ok) {
            if (what == u"send"_s) setNotice(tr("Handed to the engine."), false);
            else setNotice(tr("Saved."), false);
        } else {
            setNotice(err.isEmpty() ? tr("The engine did not do that.") : err, true);
        }
        Q_EMIT changed();
    });
    Q_EMIT changed();
}

void TakEngine::setNotice(const QString &t, bool err)
{
    m_notice = t;
    m_noticeError = err;
    Q_EMIT noticeChanged();
    m_noticeTimer.start();
}
void TakEngine::clearNotice()
{
    m_notice.clear();
    Q_EMIT noticeChanged();
}

void TakEngine::setSendingOn(bool on)
{
    if (on == m_prefs.sendingOn())
        return;
    m_prefs.setSendingOn(on);
    QString e;
    if (!m_prefs.save(&e))
        setNotice(tr("Could not save that: %1").arg(e), true);
    Q_EMIT changed();
}

QString TakEngine::sendBlocked() const
{
    if (!m_b->available())
        return m_b->unavailableReason();
    if (m_b->receiveOnly())
        return tr("The mesh engine is receive-only. Sending is off until the engine is started with sending allowed.");
    if (!m_prefs.sendingOn())
        return tr("Sending is off. Turn it on to let this app send over your radios.");
    return {};
}

bool TakEngine::canStartEngine() const
{
    if (!qEnvironmentVariableIsEmpty("TAOS_SURVIVAL_NO_SYSTEMCTL"))
        return false;
    for (const QString &d : {QDir::homePath() + u"/.config/systemd/user"_s, u"/etc/systemd/user"_s, u"/usr/lib/systemd/user"_s, u"/usr/local/lib/systemd/user"_s})
        if (QFile::exists(d + u"/taos-mesh.service"_s))
            return true;
    return false;
}

void TakEngine::startEngine()
{
    if (!canStartEngine()) {
        setNotice(tr("The mesh engine's service is not installed."), true);
        return;
    }
    QProcess::startDetached(u"systemctl"_s, {u"--user"_s, u"start"_s, u"taos-mesh.service"_s});
    setNotice(tr("Starting the mesh engine."), false);
}

QString TakEngine::kindOf(const QString &identity) const
{
    return identity.section(QLatin1Char(':'), 0, 0);
}

QString TakEngine::nameForIdentity(const QString &identity) const
{
    if (identity.isEmpty())
        return {};
    if (identity.startsWith(u"contact:"_s)) {
        const QString id = identity.mid(8);
        for (const MeshContact &c : m_b->contacts())
            if (c.id == id)
                return c.name;
    }
    for (const MeshAssignment &a : m_b->assignments())
        if (a.identity == identity && !a.name.isEmpty())
            return a.name;
    return identity.section(QLatin1Char(':'), 1);
}

QString TakEngine::displayName(const QString &node) const
{
    if (node.isEmpty() || node == u"!ffffffff"_s)
        return tr("Everyone");
    for (const MeshAssignment &a : m_b->assignments())
        if (a.node == node) {
            const QString n = nameForIdentity(a.identity);
            if (!n.isEmpty())
                return n;
        }
    for (const MeshIdentity &i : m_b->identities())
        if (i.id == node && !i.callsign.isEmpty())
            return i.callsign;
    return node;
}

QString TakEngine::ago(double ms) const { return formatAgo(qint64(ms), QDateTime::currentMSecsSinceEpoch()); }

QVariantMap TakEngine::person(const QString &node) const
{
    for (const QVariant &v : people())
        if (v.toMap().value(u"node"_s).toString() == node)
            return v.toMap();
    return {};
}

QVariantList TakEngine::people() const
{
    const auto assigns = m_b->assignments();
    const auto contacts = m_b->contacts();
    const auto places = m_b->places();
    QVariantList out;
    for (const MeshIdentity &i : m_b->identities()) {
        QVariantMap m{{u"node"_s, i.id}, {u"isSelf"_s, i.isSelf}, {u"heardMs"_s, double(i.lastHeardMs)}};
        QString identity, name = i.callsign, contactId, linked;
        for (const MeshAssignment &a : assigns)
            if (a.node == i.id) {
                identity = a.identity;
                const QString n = nameForIdentity(a.identity);
                if (!n.isEmpty())
                    name = n;
                if (a.identity.startsWith(u"contact:"_s)) {
                    contactId = a.identity.mid(8);
                    for (const MeshContact &c : contacts)
                        if (c.id == contactId)
                            linked = c.linkedAccount;
                }
            }
        if (name.isEmpty())
            name = i.id;
        QString net = networkOfNode(i.id);
        for (const MeshPlace &p : places)
            if (p.node == i.id && !p.network.isEmpty())
                net = p.network;
        if (!m_filter.isEmpty() && net != m_filter)
            continue;
        m.insert(u"network"_s, net);
        m.insert(u"networkName"_s, networkName(net));
        m.insert(u"name"_s, name);
        m.insert(u"identity"_s, identity);
        m.insert(u"kind"_s, identity.isEmpty() ? u"none"_s : kindOf(identity));
        m.insert(u"contactId"_s, contactId);
        m.insert(u"linkedAccount"_s, linked);
        bool has = i.hasPosition;
        double lon = i.lon, lat = i.lat;
        qint64 when = i.lastHeardMs;
        for (const MeshPlace &p : places)
            if (p.node == i.id) {
                has = true; lon = p.lon; lat = p.lat; when = p.positionTimeMs;
            }
        m.insert(u"hasPosition"_s, has);
        m.insert(u"lon"_s, lon);
        m.insert(u"lat"_s, lat);
        m.insert(u"positionTimeMs"_s, double(when));
        m.insert(u"coords"_s, has ? coords(lon, lat) : QString());
        m.insert(u"positionAgo"_s, has ? formatAgo(when, QDateTime::currentMSecsSinceEpoch()) : tr("no position heard"));
        m.insert(u"heardAgo"_s, formatAgo(i.lastHeardMs, QDateTime::currentMSecsSinceEpoch()));
        out.append(m);
    }
    return out;
}

QVariantList TakEngine::places() const
{
    QVariantList out;
    for (const QVariant &v : people()) {
        const QVariantMap m = v.toMap();
        if (m.value(u"hasPosition"_s).toBool())
            out.append(m);
    }
    return out;
}

QVariantList TakEngine::contacts() const
{
    QVariantList out;
    const auto assigns = m_b->assignments();
    for (const MeshContact &c : m_b->contacts()) {
        QStringList nodes;
        for (const MeshAssignment &a : assigns)
            if (a.identity == u"contact:"_s + c.id)
                nodes << a.node;
        out.append(QVariantMap{{u"id"_s, c.id}, {u"name"_s, c.name}, {u"notes"_s, c.notes}, {u"linkedAccount"_s, c.linkedAccount}, {u"told"_s, c.told},
                               {u"toldDate"_s, c.toldDate}, {u"nodes"_s, nodes}, {u"locationsHidden"_s, c.locationsHidden}});
    }
    return out;
}

void TakEngine::connectDevice(const QString &id)
{
    if (!m_connecting.isEmpty())
        return;   // one at a time: each is a handshake with a real radio
    m_connecting = id;
    m_connectErrors.remove(id);
    Q_EMIT changed();
    m_b->connectDevice(id, QString());
}

QString TakEngine::networkOfNode(const QString &node) const
{
    if (node.startsWith(u"ch:"_s))
        return node.section(QLatin1Char(':'), 1, 1);
    return node.startsWith(u'!') ? u"meshtastic"_s : u"meshcore"_s;
}

QString TakEngine::sendBlockedFor(const QString &threadId) const
{
    const QString general = sendBlocked();
    if (!general.isEmpty())
        return general;
    const QString net = networkOfNode(threadId);
    for (const MeshRadio &r : m_b->radios())
        if (r.transport == net && r.state != u"offline"_s)
            return {};   // a radio still connecting keeps the message queued in the engine
    return tr("No %1 radio is connected, so this message cannot be sent. Connect one on the Radios page.").arg(networkName(net));
}

QString TakEngine::sendVia(const QString &threadId) const
{
    const QString net = networkOfNode(threadId);
    for (const MeshRadio &r : m_b->radios())
        if (r.transport == net && r.state != u"offline"_s)
            return tr("Sends from %1 on %2").arg(r.name.isEmpty() ? r.id : r.name, networkName(net));
    return {};
}

void TakEngine::setNetworkFilter(const QString &n)
{
    if (n == m_filter)
        return;
    m_filter = n;
    Q_EMIT changed();
}

void TakEngine::setFirmware(FirmwareEngine *fw)
{
    m_fw = fw;
    if (fw)
        connect(fw, &FirmwareEngine::changed, this, &TakEngine::changed);
}

QVariantList TakEngine::networks() const
{
    QMap<QString, QPair<int, int>> by;   // radios, people
    for (const MeshRadio &r : m_b->radios())
        by[r.transport].first++;
    const QString keep = m_filter;
    for (const MeshIdentity &i : m_b->identities())
        by[networkOfNode(i.id)].second++;
    for (const MeshMessage &m : m_b->messages())
        if (!m.network.isEmpty() && !by.contains(m.network))
            by[m.network] = {0, 0};
    QVariantList out;
    for (auto it = by.cbegin(); it != by.cend(); ++it)
        out.append(QVariantMap{{u"id"_s, it.key()}, {u"name"_s, networkName(it.key())}, {u"radios"_s, it.value().first}, {u"people"_s, it.value().second}});
    Q_UNUSED(keep);
    return out;
}

QVariantMap TakEngine::radioRow(const QString &key) const
{
    for (const QVariant &v : radioRows())
        if (v.toMap().value(u"key"_s).toString() == key)
            return v.toMap();
    return {};
}

QVariantList TakEngine::radioRows() const
{
    // The firmware manager's boards, by port; the engine's open radios, by port; the ports the engine lists. A radio is one
    // row however many of those see it.
    QVariantList fwDevices = m_fw ? m_fw->devices() : QVariantList();
    auto fwFor = [&](const QString &port) -> QVariantMap {
        for (const QVariant &d : fwDevices)
            if (!port.isEmpty() && d.toMap().value(u"port"_s).toString() == port)
                return d.toMap();
        return {};
    };
    const bool fwReady = m_fw && m_fw->state() == u"ready"_s;
    const bool haveLibrary = m_fw && !m_fw->library().isEmpty();
    auto candidates = [&](const QVariantMap &fd) {
        QStringList names;
        for (const QVariant &c : fd.value(u"candidates"_s).toList())
            names << (m_fw ? m_fw->boardName(c.toString()) : c.toString());
        return names;
    };
    const auto assigns = m_b->assignments();
    QVariantList out;
    QSet<QString> seen;
    for (const MeshRadio &r : m_b->radios()) {
        const QString key = r.port.isEmpty() ? r.id : r.port;
        seen << key;
        const QVariantMap fd = fwFor(r.port);
        QString identity, who;
        for (const MeshAssignment &a : assigns)
            if (a.node == r.id) {
                identity = a.identity;
                who = nameForIdentity(a.identity);
            }
        const QString fwId = fd.value(u"id"_s).toString();
        QVariantList saved = fwId.isEmpty() ? QVariantList() : m_profiles.list(fwId);
        QVariantMap row{{u"key"_s, key}, {u"kind"_s, r.link.isEmpty() ? u"usb"_s : r.link}, {u"port"_s, r.port}, {u"engineRadioId"_s, r.id}, {u"fwDeviceId"_s, fwId},
                        {u"title"_s, r.hardware.isEmpty() ? (r.name.isEmpty() ? r.id : r.name) : r.hardware}, {u"name"_s, r.name}, {u"hardware"_s, r.hardware},
                        {u"boardGuess"_s, candidates(fd).join(u" or "_s)}, {u"network"_s, r.transport}, {u"networkName"_s, networkName(r.transport)},
                        {u"firmware"_s, r.firmware}, {u"state"_s, r.state}, {u"battery"_s, r.batteryPercent}, {u"candidates"_s, fd.value(u"candidates"_s)},
                        {u"assignedName"_s, who}, {u"assignedKind"_s, identity.isEmpty() ? u"none"_s : kindOf(identity)}, {u"identity"_s, identity},
                        {u"canConnect"_s, false}, {u"canSettings"_s, r.state == u"connected"_s}, {u"saved"_s, false}, {u"error"_s, QString()}, {u"busy"_s, false},
                        {u"canSwitch"_s, fwReady && haveLibrary && !fwId.isEmpty()}, {u"profiles"_s, saved}};
        if (!fwReady)
            row.insert(u"switchWhy"_s, tr("The firmware manager is not running."));
        else if (fwId.isEmpty())
            row.insert(u"switchWhy"_s, tr("The firmware manager does not see a board on this port, so it cannot switch it."));
        else if (!haveLibrary)
            row.insert(u"switchWhy"_s, tr("No firmware is stored yet. Open the firmware library and refresh it first."));
        out.append(row);
    }
    for (const MeshDevice &d : m_b->devices()) {
        if (seen.contains(d.id))
            continue;
        seen << d.id;
        const QVariantMap fd = d.kind == u"usb"_s ? fwFor(d.id) : QVariantMap();
        const QString fwId = fd.value(u"id"_s).toString();
        const QString guess = candidates(fd).join(u" or "_s);
        QVariantMap row{{u"key"_s, d.id}, {u"kind"_s, d.kind}, {u"port"_s, d.kind == u"usb"_s ? d.id : QString()}, {u"engineRadioId"_s, QString()}, {u"fwDeviceId"_s, fwId},
                        {u"title"_s, guess.isEmpty() ? d.label : guess}, {u"name"_s, d.label}, {u"hardware"_s, QString()}, {u"boardGuess"_s, guess}, {u"network"_s, QString()},
                        {u"networkName"_s, QString()}, {u"firmware"_s, QString()}, {u"state"_s, u"detected"_s}, {u"battery"_s, -1}, {u"candidates"_s, fd.value(u"candidates"_s)},
                        {u"assignedName"_s, QString()}, {u"assignedKind"_s, u"none"_s}, {u"identity"_s, QString()}, {u"detail"_s, d.detail}, {u"hint"_s, d.hint},
                        {u"canConnect"_s, d.supported && m_b->canConnect()}, {u"canSettings"_s, false}, {u"saved"_s, d.saved}, {u"error"_s, m_connectErrors.value(d.id).toString()},
                        {u"busy"_s, d.id == m_connecting}, {u"canSwitch"_s, fwReady && haveLibrary && !fwId.isEmpty()}, {u"profiles"_s, fwId.isEmpty() ? QVariantList() : m_profiles.list(fwId)}};
        if (!d.supported)
            row.insert(u"switchWhy"_s, tr("Bluetooth radios cannot be switched from here."));
        out.append(row);
    }
    // a board only the firmware manager sees (the mesh engine is not running, or it does not list the port)
    for (const QVariant &v : fwDevices) {
        const QVariantMap fd = v.toMap();
        const QString port = fd.value(u"port"_s).toString();
        if (port.isEmpty() || seen.contains(port))
            continue;
        const QString guess = candidates(fd).join(u" or "_s);
        out.append(QVariantMap{{u"key"_s, port}, {u"kind"_s, u"usb"_s}, {u"port"_s, port}, {u"engineRadioId"_s, QString()}, {u"fwDeviceId"_s, fd.value(u"id"_s)},
                               {u"title"_s, guess.isEmpty() ? fd.value(u"product"_s).toString() : guess}, {u"name"_s, fd.value(u"product"_s).toString()}, {u"hardware"_s, QString()},
                               {u"boardGuess"_s, guess}, {u"network"_s, QString()}, {u"networkName"_s, QString()}, {u"firmware"_s, QString()}, {u"state"_s, u"detected"_s}, {u"battery"_s, -1},
                               {u"candidates"_s, fd.value(u"candidates"_s)}, {u"assignedName"_s, QString()}, {u"assignedKind"_s, u"none"_s}, {u"identity"_s, QString()},
                               {u"detail"_s, port}, {u"hint"_s, QString()}, {u"canConnect"_s, m_b->canConnect()}, {u"canSettings"_s, false}, {u"saved"_s, false}, {u"error"_s, QString()},
                               {u"busy"_s, false}, {u"canSwitch"_s, fwReady && haveLibrary}, {u"profiles"_s, m_profiles.list(fd.value(u"id"_s).toString())}});
    }
    return out;
}

QVariantList TakEngine::devices() const
{
    QVariantList out;
    for (const MeshDevice &d : m_b->devices())
        out.append(QVariantMap{{u"id"_s, d.id}, {u"kind"_s, d.kind}, {u"label"_s, d.label}, {u"detail"_s, d.detail}, {u"hint"_s, d.hint},
                               {u"saved"_s, d.saved}, {u"supported"_s, d.supported}, {u"busy"_s, d.id == m_connecting}, {u"error"_s, m_connectErrors.value(d.id).toString()}});
    return out;
}

QVariantList TakEngine::radios() const
{
    QVariantList out;
    const auto assigns = m_b->assignments();
    for (const MeshRadio &r : m_b->radios()) {
        QString identity, who;
        for (const MeshAssignment &a : assigns)
            if (a.node == r.id) {
                identity = a.identity;
                who = nameForIdentity(a.identity);
            }
        out.append(QVariantMap{{u"id"_s, r.id}, {u"name"_s, r.name}, {u"transport"_s, r.transport}, {u"networkName"_s, networkName(r.transport)}, {u"link"_s, r.link},
                               {u"state"_s, r.state}, {u"firmware"_s, r.firmware}, {u"hardware"_s, r.hardware}, {u"port"_s, r.port}, {u"battery"_s, r.batteryPercent}, {u"identity"_s, identity},
                               {u"assignedName"_s, who}, {u"assignedKind"_s, identity.isEmpty() ? u"none"_s : kindOf(identity)}});
    }
    return out;
}

QVariantList TakEngine::threads() const
{
    struct T { QString id; qint64 last = 0; QString text; int unread = 0; int count = 0; QString network; };
    QHash<QString, T> by;
    for (const MeshMessage &m : m_b->messages()) {
        T &t = by[m.threadId];
        t.id = m.threadId;
        t.network = m.network;
        ++t.count;
        if (m.timeMs >= t.last) { t.last = m.timeMs; t.text = m.text; }
        if (!m.outgoing && m.timeMs > m_prefs.lastRead(m.threadId))
            ++t.unread;
    }
    // a channel the connected radios have is a conversation even before anyone has said anything on it: the first message
    // has to be writable somewhere
    for (const MeshRadio &r : m_b->radios()) {
        if (r.state == u"offline"_s)
            continue;
        const MeshDeviceConfig c = m_b->deviceConfig(r.id);
        if (c.state != u"ok"_s)
            continue;
        for (const QVariant &ch : c.values.value(u"channels"_s).toList()) {
            const QString id = u"ch:%1:%2"_s.arg(r.transport, ch.toMap().value(u"index"_s).toString());
            if (!by.contains(id)) {
                T &t = by[id];
                t.id = id;
                t.network = r.transport;
                t.text = tr("No messages yet");
            }
        }
    }
    QList<T> list = by.values();
    std::sort(list.begin(), list.end(), [](const T &a, const T &b) { return a.last > b.last; });
    QVariantList out;
    for (const T &t : list) {
        if (!m_filter.isEmpty() && t.network != m_filter)
            continue;
        out.append(thread(t.id).isEmpty() ? QVariantMap() : QVariantMap{{u"id"_s, t.id}, {u"title"_s, thread(t.id).value(u"title"_s)}, {u"kind"_s, thread(t.id).value(u"kind"_s)},
                                                                        {u"subtitle"_s, t.text}, {u"lastMs"_s, double(t.last)}, {u"ago"_s, t.last > 0 ? ago(double(t.last)) : QString()},
                                                                        {u"unread"_s, t.unread}, {u"count"_s, t.count}, {u"network"_s, networkName(t.network)}, {u"networkId"_s, t.network}});
    }
    return out;
}

QVariantMap TakEngine::thread(const QString &id) const
{
    if (id.isEmpty())
        return {};
    if (id.startsWith(u"ch:"_s)) {
        const QStringList p = id.split(QLatin1Char(':'));
        const QString net = p.value(1), idx = p.value(2);
        QString name;   // the channel's own name, when a connected radio has said it
        for (const MeshRadio &r : m_b->radios())
            if (r.transport == net)
                for (const QVariant &ch : m_b->deviceConfig(r.id).values.value(u"channels"_s).toList())
                    if (ch.toMap().value(u"index"_s).toString() == idx && name.isEmpty())
                        name = ch.toMap().value(u"name"_s).toString();
        return {{u"id"_s, id}, {u"kind"_s, u"channel"_s}, {u"title"_s, name.isEmpty() ? tr("%1 channel %2").arg(networkName(net), idx) : tr("%1 (%2 channel %3)").arg(name, networkName(net), idx)}};
    }
    return {{u"id"_s, id}, {u"kind"_s, u"direct"_s}, {u"title"_s, displayName(id)}, {u"node"_s, id}};
}

int TakEngine::unreadTotal() const
{
    int n = 0;
    for (const QVariant &v : threads())
        n += v.toMap().value(u"unread"_s).toInt();
    return n;
}

QVariantList TakEngine::messages(const QString &threadId) const
{
    QVariantList out;
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    for (const MeshMessage &m : m_b->messages(threadId)) {
        QString who = m.outgoing ? tr("You") : (!m.senderName.isEmpty() ? m.senderName : displayName(m.fromId));
        QString kind = m.senderIdentity.isEmpty() ? (m.outgoing ? u"person"_s : u"none"_s) : kindOf(m.senderIdentity);
        QString pos;
        if (m.hasPosition)
            pos = coords(m.lon, m.lat) + (m.positionIsLastKnown ? tr(", last known ") : tr(", ")) + formatAgo(m.positionTimeMs, now);
        QStringList sig;
        if (m.hasSignal && !m.outgoing)   // what a radio heard; it says nothing about our own send
            sig << tr("signal %1 dBm").arg(m.rssi, 0, 'f', 0) << tr("SNR %1 dB").arg(m.snr, 0, 'f', 1);
        if (m.hops >= 0 && !m.outgoing)
            sig << (m.hops == 1 ? tr("1 hop") : tr("%1 hops").arg(m.hops));
        out.append(QVariantMap{{u"id"_s, m.id}, {u"text"_s, m.text}, {u"timeMs"_s, double(m.timeMs)}, {u"ago"_s, formatAgo(m.timeMs, now)},
                               {u"time"_s, QLocale().toString(QDateTime::fromMSecsSinceEpoch(m.timeMs), QLocale::ShortFormat)}, {u"outgoing"_s, m.outgoing},
                               {u"who"_s, who}, {u"whoKind"_s, kind}, {u"fromId"_s, m.fromId}, {u"delivery"_s, m.delivery}, {u"trust"_s, m.trust},
                               {u"positionText"_s, pos}, {u"hasPosition"_s, m.hasPosition}, {u"lon"_s, m.lon}, {u"lat"_s, m.lat},
                               {u"signalText"_s, sig.join(u", "_s)}});
    }
    return out;
}

void TakEngine::markRead(const QString &threadId)
{
    qint64 last = 0;
    for (const MeshMessage &m : m_b->messages(threadId))
        last = std::max(last, m.timeMs);
    if (last <= m_prefs.lastRead(threadId))
        return;
    m_prefs.setLastRead(threadId, last);
    m_prefs.save();
    Q_EMIT changed();
}

QString TakEngine::send(const QString &threadId, const QString &text)
{
    if (text.trimmed().isEmpty())
        return tr("Type a message first.");
    const QString blocked = sendBlockedFor(threadId);
    if (!blocked.isEmpty())
        return blocked;   // nothing reaches the engine
    const MeshSendResult r = m_b->send(threadId, text.trimmed());
    return r.accepted ? QString() : (r.error.isEmpty() ? tr("The message was not accepted.") : r.error);
}

QVariantList TakEngine::trail(const QString &node) const
{
    QVariantList out;
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    for (const MeshTrailPoint &p : m_b->trail(node))
        out.append(QVariantMap{{u"lon"_s, p.lon}, {u"lat"_s, p.lat}, {u"timeMs"_s, double(p.timeMs)}, {u"ago"_s, formatAgo(p.timeMs, now)}, {u"coords"_s, coords(p.lon, p.lat)}});
    return out;
}

void TakEngine::assign(const QString &node, const QString &identity, const QString &name) { m_b->assign(node, identity, name); }
void TakEngine::createContact(const QString &name, const QString &notes, const QString &assignNode)
{
    if (name.trimmed().isEmpty()) {
        setNotice(tr("A contact needs a name."), true);
        return;
    }
    m_b->createContact(name.trimmed(), notes.trimmed(), assignNode);
}
void TakEngine::confirmTold(const QString &contactId) { m_b->setTold(contactId, true, QDate::currentDate().toString(Qt::ISODate)); }
void TakEngine::clearTold(const QString &contactId) { m_b->setTold(contactId, false, QString()); }
void TakEngine::link(const QString &contactId, const QString &accountId)
{
    if (accountId.trimmed().isEmpty()) {
        setNotice(tr("Say which taOS account this person is."), true);
        return;
    }
    QString a = accountId.trimmed();
    if (!a.startsWith(u"person:"_s))
        a = u"person:"_s + a;
    m_b->link(contactId, a);
}

QVariantMap TakEngine::config(const QString &radioId) const
{
    const MeshDeviceConfig c = m_b->deviceConfig(radioId);
    return {{u"radioId"_s, c.radioId}, {u"state"_s, c.state}, {u"note"_s, c.note}, {u"values"_s, c.values}};
}

QString TakEngine::applyConfig(const QString &radioId, const QVariantMap &changes)
{
    if (!m_b->deviceConfigWritable())
        return tr("The engine does not write radio settings yet, so nothing was changed.");
    if (!m_b->available())
        return m_b->unavailableReason();
    m_b->applyDeviceConfig(radioId, changes);
    return {};
}

QStringList TakEngine::qr(const QString &text) const
{
    const QrResult r = qrEncode(text.toUtf8(), true);
    return r.ok ? qrRows(r) : QStringList();
}

// ============================================================================================================ firmware

SwitchEngine::SwitchEngine(TakEngine *tak, FirmwareEngine *fw, FirmwareClient *client, QObject *parent) : QObject(parent), m_tak(tak), m_fw(fw), m_c(client), m_tick(new QTimer(this))
{
    m_tick->setInterval(1000);
    connect(m_tick, &QTimer::timeout, this, [this] { if (running()) Q_EMIT changed(); });
    m_tick->start();
}

QVariantList SwitchEngine::stages() const
{
    struct S { const char *id; QString label; };
    const QList<S> all{{"saving", tr("Save this radio's settings")}, {"releasing", tr("Let go of the port")}, {"flashing", tr("Write the new firmware")},
                       {"reconnecting", tr("Reconnect in the new mode")}, {"restoring", tr("Put back earlier settings")}};
    const QString cur = step();
    QVariantList out;
    bool before = true;
    const bool failed = cur == u"failed"_s, done = cur == u"done"_s;
    const QString at = failed ? failedAt() : cur;
    for (const S &s : all) {
        QString state = u"pending"_s;
        if (done)
            state = u"done"_s;
        else if (QLatin1String(s.id) == at)
            state = failed ? u"failed"_s : u"active"_s;
        else if (before && cur != u"idle"_s)
            state = u"done"_s;
        if (QLatin1String(s.id) == at)
            before = false;
        out.append(QVariantMap{{u"id"_s, QLatin1String(s.id)}, {u"label"_s, s.label}, {u"state"_s, state}});
    }
    return out;
}

QString SwitchEngine::elapsed() const
{
    const qint64 s = m_sw ? m_sw->elapsedMs() / 1000 : 0;
    return u"%1:%2"_s.arg(s / 60).arg(s % 60, 2, 10, QLatin1Char('0'));
}

void SwitchEngine::reset()
{
    if (running())
        return;
    m_sw.reset();
    Q_EMIT changed();
}

QString SwitchEngine::previousEntry(const QString &rowKey) const
{
    const QVariantMap row = m_tak->radioRow(rowKey);
    const QString fw = row.value(u"firmware"_s).toString().section(QLatin1Char(' '), 0, 0).remove(QLatin1Char('v'));
    const QString net = row.value(u"network"_s).toString();
    if (fw.isEmpty() || net.isEmpty() || !m_fw)
        return {};
    const QVariantList cands = row.value(u"candidates"_s).toList();
    for (const QVariant &e : m_fw->library()) {
        const QVariantMap m = e.toMap();
        if (m.value(u"network"_s).toString() != net)
            continue;
        if (!cands.isEmpty() && !cands.contains(m.value(u"board"_s)))
            continue;
        const QString v = m.value(u"version"_s).toString();
        if (v.contains(fw) || fw.contains(v))
            return m.value(u"id"_s).toString();
    }
    // the exact version is not stored: the newest stored one for the same network is the way back (USB build first)
    QString best;
    for (const QVariant &e : m_fw->library()) {
        const QVariantMap m = e.toMap();
        if (m.value(u"network"_s).toString() != net || (!cands.isEmpty() && !cands.contains(m.value(u"board"_s))))
            continue;
        if (m.value(u"variant"_s).toString() == u"ble"_s)
            continue;
        best = m.value(u"id"_s).toString();
        break;   // the library is newest first within a board
    }
    return best;
}

QString SwitchEngine::begin(const QString &rowKey, const QString &entryId, const QString &typedName, bool ackUnsigned)
{
    if (running())
        return tr("A switch is already running.");
    const QVariantMap row = m_tak->radioRow(rowKey);
    if (row.isEmpty())
        return tr("That radio is no longer listed.");
    if (row.value(u"fwDeviceId"_s).toString().isEmpty())
        return tr("The firmware manager does not see this board.");
    QString network;
    for (const QVariant &e : m_fw->library())
        if (e.toMap().value(u"id"_s).toString() == entryId)
            network = e.toMap().value(u"network"_s).toString();
    if (network.isEmpty())
        return tr("That firmware is not in the library.");
    SwitchRequest q;
    q.port = row.value(u"port"_s).toString();
    q.radioId = row.value(u"engineRadioId"_s).toString();
    q.deviceId = row.value(u"fwDeviceId"_s).toString();
    q.entryId = entryId;
    q.fromNetwork = row.value(u"network"_s).toString();
    q.toNetwork = network;
    q.typedName = typedName;
    q.ackUnsigned = ackUnsigned;
    q.previousEntryId = previousEntry(rowKey);
    m_previous = q.previousEntryId;
    m_subject = row;
    m_sw = std::make_unique<RadioSwitch>(m_tak->backend(), m_c, m_tak->profiles());
    connect(m_sw.get(), &RadioSwitch::changed, this, &SwitchEngine::changed);
    m_sw->start(q);
    Q_EMIT changed();
    return {};
}

FirmwareEngine::FirmwareEngine(FirmwareClient *client, const QString &account, QObject *parent) : QObject(parent), m_c(client), m_account(account) {}

static QVariantMap entryMap(const QJsonObject &e)
{
    QVariantMap m = e.toVariantMap();
    qint64 total = 0;
    for (const QJsonValue &f : e.value(u"files"_s).toArray())
        total += qint64(f.toObject().value(u"size"_s).toDouble());
    m.insert(u"sizeBytes"_s, double(total));
    const QString v = e.value(u"verification"_s).toString();
    m.insert(u"verifiedBy"_s, v == u"publisher-checksum"_s ? QObject::tr("Checked against the publisher's own checksums")
                              : v == u"release-host-digest"_s ? QObject::tr("Checked against the release host's digest") : v);
    m.insert(u"unsigned"_s, e.value(u"unsigned_by_publisher"_s).toBool());
    return m;
}

QString FirmwareEngine::sizeText(double bytes) const
{
    return QLocale().formattedDataSize(qint64(bytes), 1, QLocale::DataSizeSIFormat);
}

QString FirmwareEngine::dateText(const QString &iso) const
{
    const QDateTime d = QDateTime::fromString(iso, Qt::ISODate);
    return d.isValid() ? QLocale().toString(d.date(), u"d MMM yyyy"_s) : iso;
}

QString FirmwareEngine::totalSize() const
{
    double t = 0;
    for (const QVariant &v : m_library)
        t += v.toMap().value(u"sizeBytes"_s).toDouble();
    return sizeText(t);
}

void FirmwareEngine::reload()
{
    auto fail = [this](const FwReply &r) {
        m_state = u"offline"_s;
        m_error = r.error;
        Q_EMIT changed();
    };
    m_c->request(u"GET"_s, u"/v1/library"_s, {}, this, [this, fail](const FwReply &r) {
        if (!r.reached) { fail(r); return; }
        m_state = u"ready"_s;
        m_error.clear();
        m_library.clear();
        QList<QVariantMap> es;
        for (const QJsonValue &v : r.json.value(u"entries"_s).toArray())
            es.append(entryMap(v.toObject()));
        std::sort(es.begin(), es.end(), [](const QVariantMap &a, const QVariantMap &b) {
            return std::tuple(a.value(u"network"_s).toString(), a.value(u"board"_s).toString(), b.value(u"version"_s).toString())
                 < std::tuple(b.value(u"network"_s).toString(), b.value(u"board"_s).toString(), a.value(u"version"_s).toString());
        });
        for (const auto &m : es) m_library.append(m);
        Q_EMIT changed();
    });
    m_c->request(u"GET"_s, u"/v1/licences"_s, {}, this, [this](const FwReply &r) {
        if (!r.reached) return;
        m_pending = r.json.value(u"pending"_s).toArray().toVariantList();
        m_accepted = r.json.value(u"accepted"_s).toArray().toVariantList();
        Q_EMIT changed();
    });
    m_c->request(u"GET"_s, u"/v1/boards"_s, {}, this, [this](const FwReply &r) {
        if (!r.reached) return;
        m_boards = r.json.toVariantMap();
        Q_EMIT changed();
    });
}

void FirmwareEngine::detect()
{
    // a plain listing: it does not probe, so it never resets a board
    m_c->request(u"GET"_s, u"/v1/devices"_s, {}, this, [this](const FwReply &r) {
        if (!r.reached) { m_state = u"offline"_s; m_error = r.error; Q_EMIT changed(); return; }
        m_devices = r.json.value(u"devices"_s).toArray().toVariantList();
        Q_EMIT changed();
    });
}

void FirmwareEngine::refreshLibrary()
{
    m_busy = true;
    m_refreshNote.clear();
    Q_EMIT changed();
    m_c->request(u"POST"_s, u"/v1/library/refresh"_s, {}, this, [this](const FwReply &r) {
        m_busy = false;
        if (!r.reached || !r.error.isEmpty()) {
            m_refreshNote = r.error;
        } else {
            QStringList parts;
            for (const QJsonValue &v : r.json.value(u"reports"_s).toArray()) {
                const QJsonObject o = v.toObject();
                parts << u"%1: %2"_s.arg(networkName(o.value(u"network"_s).toString()), o.value(u"detail"_s).toString().isEmpty() ? o.value(u"status"_s).toString() : o.value(u"detail"_s).toString());
            }
            m_refreshNote = parts.join(u"; "_s);
        }
        Q_EMIT changed();
        reload();
    }, 600000);
}

void FirmwareEngine::acceptLicence(const QString &network)
{
    // called only from the Accept button: the permission is asked for now, for this network, and used once
    m_c->mintNonce(u"licence.accept"_s, network, this, [this, network](const QString &nonce, const QString &err) {
        if (nonce.isEmpty()) {
            m_refreshNote = err;
            Q_EMIT changed();
            return;
        }
        m_c->request(u"POST"_s, u"/v1/licences/accept"_s, QJsonObject{{u"network"_s, network}, {u"by"_s, m_account.isEmpty() ? u"owner"_s : m_account}, {u"owner_nonce"_s, nonce}}, this,
                     [this](const FwReply &r) {
                         if (!r.error.isEmpty()) {
                             m_refreshNote = r.error;
                             Q_EMIT changed();
                             return;
                         }
                         reload();
                     });
    });
}

void FirmwareEngine::loadDeviceFirmware(const QString &deviceId)
{
    m_devFw.clear();
    Q_EMIT changed();
    m_c->request(u"GET"_s, u"/v1/devices/"_s + deviceId + u"/firmware"_s, {}, this, [this](const FwReply &r) {
        if (!r.reached) return;
        for (const QJsonValue &v : r.json.value(u"firmware"_s).toArray())
            m_devFw.append(entryMap(v.toObject()));
        Q_EMIT changed();
    });
}

void FirmwareEngine::planFlash(const QString &deviceId, const QString &entryId)
{
    m_plan.clear();
    m_planError.clear();
    Q_EMIT planChanged();
    m_c->request(u"POST"_s, u"/v1/flash/plan"_s, QJsonObject{{u"device"_s, deviceId}, {u"entry"_s, entryId}}, this, [this](const FwReply &r) {
        if (!r.reached)
            m_planError = r.error;
        else if (!r.json.value(u"ok"_s).toBool())
            m_planError = r.json.value(u"message"_s).toString().isEmpty() ? r.error : r.json.value(u"message"_s).toString();
        else
            m_plan = r.json.value(u"plan"_s).toObject().toVariantMap();
        Q_EMIT planChanged();
    });
}

void FirmwareEngine::clearPlan()
{
    m_plan.clear();
    m_planError.clear();
    Q_EMIT planChanged();
}

QString FirmwareEngine::boardName(const QString &boardId) const
{
    for (const QJsonValue &b : QJsonArray::fromVariantList(m_boards.value(u"boards"_s).toList()))
        if (b.toObject().value(u"id"_s).toString() == boardId)
            return b.toObject().value(u"name"_s).toString();
    return boardId;
}

void FirmwareEngine::runFlash(const QString &deviceId, const QString &entryId, const QString &typedName, bool ackUnsigned, bool allowNoBackup)
{
    // The engine checks the typed name and the acknowledgement again; this side only refuses to send nothing at all.
    // Called only from the Flash button: one permission is asked for now, for this device and image, and used once.
    if (typedName.trimmed().isEmpty() || m_plan.isEmpty()) {
        m_result = {{u"ok"_s, false}, {u"message"_s, tr("Type the board name to confirm.")}};
        Q_EMIT resultChanged();
        return;
    }
    m_busy = true;
    Q_EMIT changed();
    m_c->mintNonce(u"flash.run"_s, deviceId + u"|"_s + entryId, this, [this, deviceId, entryId, typedName, ackUnsigned, allowNoBackup](const QString &nonce, const QString &err) {
        if (nonce.isEmpty()) {
            m_busy = false;
            m_result = {{u"ok"_s, false}, {u"message"_s, tr("The firmware manager did not give permission to flash: %1").arg(err)}};
            Q_EMIT resultChanged();
            Q_EMIT changed();
            return;
        }
        QJsonObject body{{u"device"_s, deviceId}, {u"entry"_s, entryId}, {u"confirm"_s, typedName.trimmed()}, {u"owner_nonce"_s, nonce},
                         {u"actor"_s, u"person"_s}};   // exactly "person": the engine takes no other word for the human at the screen
        if (ackUnsigned)
            body.insert(u"ack_unsigned"_s, true);
        if (allowNoBackup)
            body.insert(u"allow_no_backup"_s, true);
        m_c->request(u"POST"_s, u"/v1/flash/run"_s, body, this, [this](const FwReply &r) {
            m_busy = false;
            m_result = r.json.toVariantMap();
            if (!r.reached)
                m_result = {{u"ok"_s, false}, {u"message"_s, r.error}};
            else if (r.http == 403)
                m_result = {{u"ok"_s, false}, {u"message"_s, tr("The firmware manager refused: the owner permission was not accepted.")}};
            Q_EMIT resultChanged();
            Q_EMIT changed();
        }, 1800000);
    });
}

void FirmwareEngine::clearResult()
{
    m_result.clear();
    Q_EMIT resultChanged();
}

void FirmwareEngine::startService()
{
    if (!qEnvironmentVariableIsEmpty("TAOS_SURVIVAL_NO_SYSTEMCTL"))
        return;
    QProcess::startDetached(u"systemctl"_s, {u"--user"_s, u"start"_s, u"taos-firmware.service"_s});
}

} // namespace Taos::Survival
