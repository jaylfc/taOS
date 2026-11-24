// SPDX-License-Identifier: AGPL-3.0-or-later
#include "bluezclient.h"

#include <QDBusVariant>
#include <algorithm>

using namespace dbusutil;

namespace {
const char *kObjMgr = "org.freedesktop.DBus.ObjectManager";
const char *kAdapter = "org.bluez.Adapter1";
const char *kDevice = "org.bluez.Device1";
const char *kBattery = "org.bluez.Battery1";
const char *kAgentMgr = "org.bluez.AgentManager1";
QString q(const QString &s) { return QStringLiteral("“") + s + QStringLiteral("”"); }
}  // namespace

// ---- models --------------------------------------------------------------

QString BtModel::describe(const BtEntry &e) {
    QStringList p;
    p << e.name;
    if (e.busy) p << QObject::tr("working");
    else if (e.paired) p << (e.connected ? QObject::tr("connected") : QObject::tr("not connected"));
    else p << QObject::tr("nearby, not paired");
    if (e.battery >= 0) p << QObject::tr("battery %1 percent").arg(e.battery);
    return p.join(QStringLiteral(", "));
}

QHash<int, QByteArray> BtModel::roleNames() const {
    return {{PathRole, "path"}, {NameRole, "name"}, {PairedRole, "paired"}, {ConnectedRole, "connected"},
            {BatteryRole, "battery"}, {BusyRole, "busy"}, {RssiRole, "rssi"}, {DescriptionRole, "description"},
            {IconRole, "icon"}};
}

QVariant BtModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() < 0 || i.row() >= m_rows.size()) return {};
    const BtEntry &e = m_rows.at(i.row());
    switch (role) {
    case PathRole: return e.path;
    case NameRole: case Qt::DisplayRole: return e.name;
    case PairedRole: return e.paired;
    case ConnectedRole: return e.connected;
    case BatteryRole: return e.battery;
    case BusyRole: return e.busy;
    case RssiRole: return e.rssi;
    case DescriptionRole: return describe(e);
    case IconRole: return e.icon;
    case Qt::UserRole + 100: return e.hasName;
    }
    return {};
}

int BtModel::find(const QString &path) const {
    for (int i = 0; i < m_rows.size(); ++i)
        if (m_rows.at(i).path == path) return i;
    return -1;
}

const BtEntry *BtModel::entry(const QString &path) const {
    const int i = find(path);
    return i < 0 ? nullptr : &m_rows.at(i);
}

void BtModel::upsert(const BtEntry &e) {
    const int i = find(e.path);
    if (i < 0) {
        beginInsertRows({}, int(m_rows.size()), int(m_rows.size()));
        m_rows.append(e);
        endInsertRows();
    } else {
        m_rows[i] = e;
        emit dataChanged(index(i), index(i));
    }
}

void BtModel::remove(const QString &path) {
    const int i = find(path);
    if (i < 0) return;
    beginRemoveRows({}, i, i);
    m_rows.removeAt(i);
    endRemoveRows();
}

void BtModel::clear() {
    beginResetModel();
    m_rows.clear();
    endResetModel();
}

bool BtFilterModel::filterAcceptsRow(int row, const QModelIndex &parent) const {
    const QModelIndex i = sourceModel()->index(row, 0, parent);
    const bool paired = i.data(BtModel::PairedRole).toBool();
    if (m_paired) return paired;
    return !paired && i.data(Qt::UserRole + 100).toBool();  // nearby: only devices that gave a name
}

bool BtFilterModel::lessThan(const QModelIndex &a, const QModelIndex &b) const {
    if (m_paired) {
        const bool ca = a.data(BtModel::ConnectedRole).toBool(), cb = b.data(BtModel::ConnectedRole).toBool();
        if (ca != cb) return ca;
    } else {
        const int ra = a.data(BtModel::RssiRole).toInt() / 10, rb = b.data(BtModel::RssiRole).toInt() / 10;
        if (ra != rb) return ra > rb;
    }
    return a.data(BtModel::NameRole).toString().compare(b.data(BtModel::NameRole).toString(), Qt::CaseInsensitive) < 0;
}

// ---- agent ---------------------------------------------------------------

BluezAgent::BluezAgent(const QDBusConnection &bus, QObject *parent) : QObject(parent), m_bus(bus) {
    new BluezAgentAdaptor(this);
}

void BluezAgent::request(Kind k, const QString &device, const QString &code, const QDBusMessage &msg) {
    if (m_pending) respond(false, QString());  // one question at a time: the older one is declined
    msg.setDelayedReply(true);
    m_msg = msg;
    m_pending = true;
    m_kind = k;
    emit requested(int(k), device, code);
}

void BluezAgent::display(Kind k, const QString &device, const QString &code) { emit requested(int(k), device, code); }

void BluezAgent::respond(bool accept, const QString &value) {
    if (!m_pending) return;
    const QDBusMessage msg = m_msg;
    const Kind k = m_kind;
    m_pending = false;
    m_msg = QDBusMessage();
    QDBusMessage reply;
    if (!accept) {
        reply = msg.createErrorReply(QStringLiteral("org.bluez.Error.Rejected"), QStringLiteral("Rejected"));
    } else if (k == Pin) {
        reply = msg.createReply(value);
    } else if (k == Passkey) {
        reply = msg.createReply(QVariant::fromValue(value.toUInt()));
    } else {
        reply = msg.createReply();
    }
    // Reply on the connection the call arrived on.
    m_bus.send(reply);
}

void BluezAgent::cancel() {
    if (m_pending) {
        m_pending = false;
        m_msg = QDBusMessage();
    }
    emit cancelled();
}

QString BluezAgentAdaptor::RequestPinCode(const QDBusObjectPath &d, const QDBusMessage &m) {
    m_a->request(BluezAgent::Pin, d.path(), QString(), m);
    return {};
}
void BluezAgentAdaptor::DisplayPinCode(const QDBusObjectPath &d, const QString &pin) {
    m_a->display(BluezAgent::DisplayPin, d.path(), pin);
}
uint BluezAgentAdaptor::RequestPasskey(const QDBusObjectPath &d, const QDBusMessage &m) {
    m_a->request(BluezAgent::Passkey, d.path(), QString(), m);
    return 0;
}
void BluezAgentAdaptor::DisplayPasskey(const QDBusObjectPath &d, uint passkey, ushort) {
    m_a->display(BluezAgent::DisplayPasskey, d.path(), QStringLiteral("%1").arg(passkey, 6, 10, QLatin1Char('0')));
}
void BluezAgentAdaptor::RequestConfirmation(const QDBusObjectPath &d, uint passkey, const QDBusMessage &m) {
    m_a->request(BluezAgent::Confirm, d.path(), QStringLiteral("%1").arg(passkey, 6, 10, QLatin1Char('0')), m);
}
void BluezAgentAdaptor::RequestAuthorization(const QDBusObjectPath &d, const QDBusMessage &m) {
    m_a->request(BluezAgent::Authorize, d.path(), QString(), m);
}
void BluezAgentAdaptor::AuthorizeService(const QDBusObjectPath &d, const QString &, const QDBusMessage &m) {
    m_a->request(BluezAgent::Authorize, d.path(), QString(), m);
}

// ---- client --------------------------------------------------------------

BluezClient::BluezClient(const QDBusConnection &bus, QObject *parent)
    : QObject(parent), m_bus(bus), m_watcher(m_service, bus, QDBusServiceWatcher::WatchForOwnerChange), m_agent(bus) {
    registerTypes();
    m_pairedModel.setSourceModel(&m_model);
    m_nearbyModel.setSourceModel(&m_model);
    for (QAbstractItemModel *m : {static_cast<QAbstractItemModel *>(&m_pairedModel), static_cast<QAbstractItemModel *>(&m_nearbyModel)}) {
        connect(m, &QAbstractItemModel::rowsInserted, this, &BluezClient::countsChanged);
        connect(m, &QAbstractItemModel::rowsRemoved, this, &BluezClient::countsChanged);
        connect(m, &QAbstractItemModel::modelReset, this, &BluezClient::countsChanged);
        connect(m, &QAbstractItemModel::layoutChanged, this, &BluezClient::countsChanged);
    }
    m_pairedModel.sort(0);
    m_nearbyModel.sort(0);
    connect(&m_watcher, &QDBusServiceWatcher::serviceRegistered, this, [this] { refreshAll(); });
    connect(&m_watcher, &QDBusServiceWatcher::serviceUnregistered, this, [this] { clearAll(); });
    connect(&m_agent, &BluezAgent::requested, this, [this](int k, const QString &d, const QString &c) { onAgentRequest(k, d, c); });
    connect(&m_agent, &BluezAgent::cancelled, this, [this] {
        if (m_reqKind) { m_reqKind = 0; emit requestChanged(); }
    });
    QDBusConnection b = m_bus;
    b.registerObject(m_agentPath, &m_agent, QDBusConnection::ExportAdaptors);
}

void BluezClient::start() {
    m_bus.connect(m_service, QStringLiteral("/"), QString::fromLatin1(kObjMgr), QStringLiteral("InterfacesAdded"), this, SLOT(onAdded(QDBusMessage)));
    m_bus.connect(m_service, QStringLiteral("/"), QString::fromLatin1(kObjMgr), QStringLiteral("InterfacesRemoved"), this, SLOT(onRemoved(QDBusMessage)));
    m_bus.connect(m_service, QString(), QStringLiteral("org.freedesktop.DBus.Properties"), QStringLiteral("PropertiesChanged"), this, SLOT(onProps(QDBusMessage)));
    refreshAll();
}

QString BluezClient::message() const {
    if (!m_available) return tr("Bluetooth is not available on this computer");
    if (!adapterPresent()) return tr("This computer has no Bluetooth adapter");
    if (!m_powered) return tr("Bluetooth is off");
    return {};
}

void BluezClient::clearAll() {
    m_available = false;
    m_adapter.clear();
    m_powered = m_discovering = m_agentRegistered = false;
    m_model.clear();
    emit changed();
}

void BluezClient::refreshAll() {
    call(m_bus, m_service, QStringLiteral("/"), QString::fromLatin1(kObjMgr), QStringLiteral("GetManagedObjects"), {}, this,
         [this](const QDBusMessage &r) {
             if (isError(r)) {  // not installed, not running, or not activatable
                 m_available = false;
                 emit changed();
                 return;
             }
             m_available = true;
             const DBusManagedObjects all = toManaged(r.arguments().value(0));
             for (auto it = all.cbegin(); it != all.cend(); ++it) applyInterfaces(it.key().path(), it.value());
             emit changed();
             if (m_active) setActive(true);
         });
}

void BluezClient::applyInterfaces(const QString &path, const DBusSettings &ifaces) {
    if (ifaces.contains(QString::fromLatin1(kAdapter))) {
        if (m_adapter.isEmpty()) m_adapter = path;
        const QVariantMap p = ifaces.value(QString::fromLatin1(kAdapter));
        if (p.contains(QStringLiteral("Powered"))) m_powered = p.value(QStringLiteral("Powered")).toBool();
        if (p.contains(QStringLiteral("Discovering"))) m_discovering = p.value(QStringLiteral("Discovering")).toBool();
    }
    if (ifaces.contains(QString::fromLatin1(kDevice)) || ifaces.contains(QString::fromLatin1(kBattery))) {
        const BtEntry *old = m_model.entry(path);
        BtEntry e = old ? *old : BtEntry();
        e.path = path;
        const QVariantMap d = ifaces.value(QString::fromLatin1(kDevice));
        if (d.contains(QStringLiteral("Address"))) e.address = d.value(QStringLiteral("Address")).toString();
        if (d.contains(QStringLiteral("Name"))) { e.hasName = true; e.name = d.value(QStringLiteral("Name")).toString(); }
        if (d.contains(QStringLiteral("Alias")) && !e.hasName) e.name = d.value(QStringLiteral("Alias")).toString();
        if (d.contains(QStringLiteral("Alias")) && e.hasName) e.name = d.value(QStringLiteral("Alias")).toString();
        if (d.contains(QStringLiteral("Paired"))) e.paired = d.value(QStringLiteral("Paired")).toBool();
        if (d.contains(QStringLiteral("Connected"))) e.connected = d.value(QStringLiteral("Connected")).toBool();
        if (d.contains(QStringLiteral("Trusted"))) e.trusted = d.value(QStringLiteral("Trusted")).toBool();
        if (d.contains(QStringLiteral("RSSI"))) e.rssi = d.value(QStringLiteral("RSSI")).toInt();
        if (d.contains(QStringLiteral("Icon"))) e.icon = d.value(QStringLiteral("Icon")).toString();
        const QVariantMap b = ifaces.value(QString::fromLatin1(kBattery));
        if (b.contains(QStringLiteral("Percentage"))) e.battery = b.value(QStringLiteral("Percentage")).toInt();
        if (e.name.isEmpty()) e.name = e.address;
        m_model.upsert(e);
    }
}

void BluezClient::onAdded(const QDBusMessage &m) {
    const QList<QVariant> a = m.arguments();
    if (a.size() < 2) return;
    applyInterfaces(toPaths(a.at(0)).value(0), toSettings(a.at(1)));
    emit changed();
}

void BluezClient::onRemoved(const QDBusMessage &m) {
    const QList<QVariant> a = m.arguments();
    if (a.size() < 2) return;
    const QString path = toPaths(a.at(0)).value(0);
    const QVariant gv = unwrap(a.at(1));
    const QStringList gone = gv.metaType() == QMetaType::fromType<QDBusArgument>()
                                 ? qdbus_cast<QStringList>(gv.value<QDBusArgument>())
                                 : gv.toStringList();
    if (gone.contains(QString::fromLatin1(kDevice))) m_model.remove(path);
    if (gone.contains(QString::fromLatin1(kAdapter)) && path == m_adapter) {
        m_adapter.clear();
        m_powered = m_discovering = false;
    }
    emit changed();
}

void BluezClient::onProps(const QDBusMessage &m) {
    const QList<QVariant> a = m.arguments();
    if (a.size() < 2) return;
    const QString iface = a.at(0).toString();
    const QVariantMap p = toMap(a.at(1));
    const QString path = m.path();
    if (iface == QLatin1String(kAdapter)) {
        if (path != m_adapter) return;
        if (p.contains(QStringLiteral("Powered"))) {
            const bool was = m_powered;
            m_powered = p.value(QStringLiteral("Powered")).toBool();
            if (was != m_powered && m_active) {
                if (m_powered) startDiscovery();
            }
        }
        if (p.contains(QStringLiteral("Discovering"))) m_discovering = p.value(QStringLiteral("Discovering")).toBool();
        emit changed();
    } else if (iface == QLatin1String(kDevice) || iface == QLatin1String(kBattery)) {
        if (!m_model.entry(path)) return;
        DBusSettings s;
        s.insert(iface, p);
        applyInterfaces(path, s);
        emit changed();
    }
}

QString BluezClient::nameOf(const QString &path) const {
    const BtEntry *e = m_model.entry(path);
    return e ? e->name : tr("the device");
}

QString BluezClient::errorWords(const QString &n, const QString &dev) {
    const QString d = q(dev);
    if (n.endsWith(QLatin1String("AuthenticationCanceled"))) return tr("Pairing with %1 was cancelled.").arg(d);
    if (n.endsWith(QLatin1String("AuthenticationRejected")) || n.endsWith(QLatin1String("AuthenticationFailed")))
        return tr("%1 did not accept the pairing.").arg(d);
    if (n.endsWith(QLatin1String("AuthenticationTimeout"))) return tr("Pairing with %1 timed out.").arg(d);
    if (n.endsWith(QLatin1String("ConnectionAttemptFailed")) || n.endsWith(QLatin1String("Failed")) || n.endsWith(QLatin1String("NotReady")))
        return tr("Could not reach %1. Make sure it is on and close by.").arg(d);
    if (n.endsWith(QLatin1String("AlreadyConnected"))) return tr("%1 is already connected.").arg(d);
    if (n.endsWith(QLatin1String("InProgress"))) return tr("Another Bluetooth action is still running.");
    if (n.contains(QLatin1String("AccessDenied")) || n.contains(QLatin1String("NotAuthorized")))
        return tr("This account is not allowed to use Bluetooth on this computer.");
    return tr("Could not finish with %1.").arg(d);
}

void BluezClient::setError(const QString &t) {
    m_error = t;
    emit errorChanged();
    if (!t.isEmpty()) emit announcement(t);
}
void BluezClient::clearError() { if (!m_error.isEmpty()) { m_error.clear(); emit errorChanged(); } }

// ---- actions -------------------------------------------------------------

void BluezClient::setActive(bool on) {
    m_active = on;
    if (!m_available || m_adapter.isEmpty()) return;
    if (on) {
        registerAgent();
        if (m_powered) startDiscovery();
    } else {
        stopDiscovery();
        unregisterAgent();
    }
}

void BluezClient::startDiscovery() {
    if (m_adapter.isEmpty() || m_discovering) return;
    call(m_bus, m_service, m_adapter, QString::fromLatin1(kAdapter), QStringLiteral("StartDiscovery"), {}, this);
}

void BluezClient::stopDiscovery() {
    if (m_adapter.isEmpty()) return;
    call(m_bus, m_service, m_adapter, QString::fromLatin1(kAdapter), QStringLiteral("StopDiscovery"), {}, this);
}

void BluezClient::registerAgent() {
    if (m_agentRegistered) return;
    m_agentRegistered = true;
    const QVariant path = QVariant::fromValue(QDBusObjectPath(m_agentPath));
    call(m_bus, m_service, QStringLiteral("/org/bluez"), QString::fromLatin1(kAgentMgr), QStringLiteral("RegisterAgent"),
         {path, QStringLiteral("KeyboardDisplay")}, this, [this, path](const QDBusMessage &) {
             call(m_bus, m_service, QStringLiteral("/org/bluez"), QString::fromLatin1(kAgentMgr),
                  QStringLiteral("RequestDefaultAgent"), {path}, this);
         });
}

void BluezClient::unregisterAgent() {
    if (!m_agentRegistered) return;
    m_agentRegistered = false;
    m_agent.cancel();
    call(m_bus, m_service, QStringLiteral("/org/bluez"), QString::fromLatin1(kAgentMgr), QStringLiteral("UnregisterAgent"),
         {QVariant::fromValue(QDBusObjectPath(m_agentPath))}, this);
}

void BluezClient::setPowered(bool on) {
    if (m_adapter.isEmpty()) return;
    dbusutil::setProperty(m_bus, m_service, m_adapter, QString::fromLatin1(kAdapter), QStringLiteral("Powered"), on, this,
                [this, on](const QDBusMessage &r) {
                    if (isError(r)) { setError(tr("This account is not allowed to turn Bluetooth on or off.")); return; }
                    m_powered = on;
                    if (!on) m_discovering = false;
                    emit changed();
                    emit announcement(on ? tr("Bluetooth turned on") : tr("Bluetooth turned off"));
                    if (on && m_active) startDiscovery();
                });
}

static void setBusy(BtModel *m, const QString &path, bool busy) {
    if (const BtEntry *e = m->entry(path)) {
        BtEntry c = *e;
        c.busy = busy;
        m->upsert(c);
    }
}

void BluezClient::pair(const QString &path) {
    const QString name = nameOf(path);
    clearError();
    setBusy(&m_model, path, true);
    emit announcement(tr("Pairing with %1").arg(q(name)));
    call(m_bus, m_service, path, QString::fromLatin1(kDevice), QStringLiteral("Pair"), {}, this, [this, path, name](const QDBusMessage &r) {
        if (m_reqKind) { m_reqKind = 0; emit requestChanged(); }
        if (isError(r)) {
            setBusy(&m_model, path, false);
            if (!r.errorName().endsWith(QLatin1String("AlreadyExists"))) {
                setError(errorWords(r.errorName(), name));
                return;
            }
        }
        // Paired: trust it so it may reconnect by itself, then connect now.
        dbusutil::setProperty(m_bus, m_service, path, QString::fromLatin1(kDevice), QStringLiteral("Trusted"), true, this);
        emit announcement(tr("Paired with %1").arg(q(name)));
        connectDevice(path);
    }, 90000);
}

void BluezClient::connectDevice(const QString &path) {
    const QString name = nameOf(path);
    clearError();
    setBusy(&m_model, path, true);
    call(m_bus, m_service, path, QString::fromLatin1(kDevice), QStringLiteral("Connect"), {}, this, [this, path, name](const QDBusMessage &r) {
        setBusy(&m_model, path, false);
        if (isError(r)) setError(errorWords(r.errorName(), name));
        else emit announcement(tr("Connected to %1").arg(q(name)));
    }, 40000);
}

void BluezClient::disconnectDevice(const QString &path) {
    const QString name = nameOf(path);
    setBusy(&m_model, path, true);
    call(m_bus, m_service, path, QString::fromLatin1(kDevice), QStringLiteral("Disconnect"), {}, this, [this, path, name](const QDBusMessage &r) {
        setBusy(&m_model, path, false);
        if (isError(r)) setError(errorWords(r.errorName(), name));
        else emit announcement(tr("Disconnected from %1").arg(q(name)));
    });
}

void BluezClient::forget(const QString &path) {
    if (m_adapter.isEmpty()) return;
    const QString name = nameOf(path);
    call(m_bus, m_service, m_adapter, QString::fromLatin1(kAdapter), QStringLiteral("RemoveDevice"),
         {QVariant::fromValue(QDBusObjectPath(path))}, this, [this, name](const QDBusMessage &r) {
             if (isError(r)) setError(errorWords(r.errorName(), name));
             else emit announcement(tr("Forgot %1").arg(q(name)));
         });
}

void BluezClient::cancelPairing() {
    if (m_agent.pending()) m_agent.respond(false, QString());
    if (m_reqKind) { m_reqKind = 0; emit requestChanged(); }
    for (int i = 0; i < m_model.rowCount(); ++i) {
        const QString p = m_model.index(i).data(BtModel::PathRole).toString();
        if (m_model.index(i).data(BtModel::BusyRole).toBool() && !m_model.index(i).data(BtModel::PairedRole).toBool())
            call(m_bus, m_service, p, QString::fromLatin1(kDevice), QStringLiteral("CancelPairing"), {}, this);
    }
}

void BluezClient::respond(bool accept, const QString &value) {
    m_agent.respond(accept, value);
    if (m_reqKind) { m_reqKind = 0; emit requestChanged(); }
}

void BluezClient::onAgentRequest(int kind, const QString &dev, const QString &code) {
    m_reqKind = kind;
    m_reqDevice = nameOf(dev);
    m_reqCode = code;
    emit requestChanged();
    switch (kind) {
    case BluezAgent::Confirm: emit announcement(tr("Check that %1 shows the code %2").arg(q(m_reqDevice), code)); break;
    case BluezAgent::DisplayPasskey: case BluezAgent::DisplayPin: emit announcement(tr("Type %1 on %2").arg(code, q(m_reqDevice))); break;
    case BluezAgent::Authorize: emit announcement(tr("%1 wants to connect").arg(q(m_reqDevice))); break;
    default: emit announcement(tr("%1 needs a code to pair").arg(q(m_reqDevice)));
    }
    if (!m_active) emit attention();
}
