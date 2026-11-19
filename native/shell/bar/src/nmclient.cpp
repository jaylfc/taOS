#include "nmclient.h"

#include <QDBusObjectPath>
#include <QDBusVariant>
#include <QLocale>
#include <QUuid>
#include <algorithm>

#include "dbusutil.h"

using namespace dbusutil;

namespace {
const char *kNm = "org.freedesktop.NetworkManager";
const char *kRoot = "/org/freedesktop/NetworkManager";
const char *kDevice = "org.freedesktop.NetworkManager.Device";
const char *kWireless = "org.freedesktop.NetworkManager.Device.Wireless";
const char *kWired = "org.freedesktop.NetworkManager.Device.Wired";
const char *kAp = "org.freedesktop.NetworkManager.AccessPoint";
const char *kSettings = "/org/freedesktop/NetworkManager/Settings";
const char *kSettingsIface = "org.freedesktop.NetworkManager.Settings";
const char *kConn = "org.freedesktop.NetworkManager.Settings.Connection";

enum { DevEthernet = 1, DevWifi = 2 };
enum { StUnavailable = 20, StDisconnected = 30, StPrepare = 40, StSecondaries = 90, StActivated = 100, StFailed = 120 };
enum { KeyPsk = 0x100, KeyEap = 0x200, KeySae = 0x400 };

QString q(const QString &ssid) { return QStringLiteral("“") + ssid + QStringLiteral("”"); }
bool authReason(uint r) { return r >= 7 && r <= 11; }
}  // namespace

// ---- model ---------------------------------------------------------------

QString WifiModel::strengthWord(int s) {
    if (s >= 67) return tr("strong");
    if (s >= 34) return tr("fair");
    return tr("weak");
}

QString WifiModel::describe(const WifiEntry &e) {
    QStringList p;
    p << e.ssid;
    if (e.active) p << tr("connected");
    else if (e.connecting) p << tr("connecting");
    else if (e.known) p << tr("saved");
    p << tr("signal %1").arg(strengthWord(e.strength));
    if (e.secured) p << tr("secured");
    if (!e.supported) p << tr("needs a work or school sign-in, not supported here");
    return p.join(QStringLiteral(", "));
}

QHash<int, QByteArray> WifiModel::roleNames() const {
    return {{SsidRole, "ssid"}, {StrengthRole, "strength"}, {SecuredRole, "secured"}, {ActiveRole, "active"},
            {KnownRole, "known"}, {ConnectingRole, "connecting"}, {SupportedRole, "supported"},
            {DescriptionRole, "description"}};
}

QVariant WifiModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() < 0 || i.row() >= m_rows.size()) return {};
    const WifiEntry &e = m_rows.at(i.row());
    switch (role) {
    case SsidRole: case Qt::DisplayRole: return e.ssid;
    case StrengthRole: return e.strength;
    case SecuredRole: return e.secured;
    case ActiveRole: return e.active;
    case KnownRole: return e.known;
    case ConnectingRole: return e.connecting;
    case SupportedRole: return e.supported;
    case DescriptionRole: return describe(e);
    }
    return {};
}

void WifiModel::setRows(const QList<WifiEntry> &rows) {
    bool same = rows.size() == m_rows.size();
    for (int i = 0; same && i < rows.size(); ++i) same = rows.at(i).ssid == m_rows.at(i).ssid;
    if (!same) {
        beginResetModel();
        m_rows = rows;
        endResetModel();
        return;
    }
    // Same networks in the same order: update in place so focus and hover survive.
    for (int i = 0; i < rows.size(); ++i) {
        const WifiEntry &a = m_rows.at(i), &b = rows.at(i);
        if (a.strength != b.strength || a.secured != b.secured || a.active != b.active || a.known != b.known
            || a.connecting != b.connecting || a.supported != b.supported) {
            m_rows[i] = b;
            emit dataChanged(index(i), index(i));
        } else {
            m_rows[i].apPath = b.apPath;
        }
    }
}

// ---- client --------------------------------------------------------------

NmClient::NmClient(const QDBusConnection &bus, QObject *parent)
    : QObject(parent), m_bus(bus), m_watcher(m_service, bus, QDBusServiceWatcher::WatchForOwnerChange) {
    registerTypes();
    m_rebuild.setSingleShot(true);
    m_rebuild.setInterval(60);
    connect(&m_rebuild, &QTimer::timeout, this, &NmClient::rebuild);
    m_scanTimer.setInterval(20000);
    m_scanTimer.setTimerType(Qt::VeryCoarseTimer);
    connect(&m_scanTimer, &QTimer::timeout, this, &NmClient::scan);
    connect(&m_watcher, &QDBusServiceWatcher::serviceRegistered, this, [this] { refreshAll(); });
    connect(&m_watcher, &QDBusServiceWatcher::serviceUnregistered, this, [this] { clearAll(); });
}

void NmClient::start() {
    const QString svc = m_service;
    const QString P = QStringLiteral("org.freedesktop.DBus.Properties");
    m_bus.connect(svc, QString(), P, QStringLiteral("PropertiesChanged"), this, SLOT(onProps(QDBusMessage)));
    m_bus.connect(svc, QString::fromLatin1(kRoot), QString::fromLatin1(kNm), QStringLiteral("DeviceAdded"), this,
                  SLOT(onDeviceAdded(QDBusMessage)));
    m_bus.connect(svc, QString::fromLatin1(kRoot), QString::fromLatin1(kNm), QStringLiteral("DeviceRemoved"), this,
                  SLOT(onDeviceRemoved(QDBusMessage)));
    m_bus.connect(svc, QString(), QString::fromLatin1(kWireless), QStringLiteral("AccessPointAdded"), this,
                  SLOT(onApAdded(QDBusMessage)));
    m_bus.connect(svc, QString(), QString::fromLatin1(kWireless), QStringLiteral("AccessPointRemoved"), this,
                  SLOT(onApRemoved(QDBusMessage)));
    m_bus.connect(svc, QString(), QString::fromLatin1(kDevice), QStringLiteral("StateChanged"), this,
                  SLOT(onDeviceState(QDBusMessage)));
    m_bus.connect(svc, QString::fromLatin1(kSettings), QString::fromLatin1(kSettingsIface), QStringLiteral("NewConnection"),
                  this, SLOT(onSettingsChange(QDBusMessage)));
    m_bus.connect(svc, QString::fromLatin1(kSettings), QString::fromLatin1(kSettingsIface),
                  QStringLiteral("ConnectionRemoved"), this, SLOT(onSettingsChange(QDBusMessage)));
    call(m_bus, QStringLiteral("org.freedesktop.DBus"), QStringLiteral("/org/freedesktop/DBus"),
         QStringLiteral("org.freedesktop.DBus"), QStringLiteral("NameHasOwner"), {svc}, this,
         [this](const QDBusMessage &r) {
             if (!isError(r) && r.arguments().value(0).toBool()) refreshAll();
             else emit changed();
         });
}

void NmClient::clearAll() {
    m_available = false;
    m_wifiDev.clear(); m_ethDev.clear(); m_activeAp.clear();
    m_aps.clear(); m_known.clear();
    m_wifiOn = false; m_wifiHw = true; m_wifiState = m_ethState = 0;
    m_attemptSsid.clear();
    m_model.setRows({});
    emit changed();
}

void NmClient::refreshAll() {
    m_available = true;
    getAll(m_bus, m_service, QString::fromLatin1(kRoot), QString::fromLatin1(kNm), this, [this](const QVariantMap &p, bool ok) {
        if (!ok) return;
        m_wifiOn = p.value(QStringLiteral("WirelessEnabled")).toBool();
        m_wifiHw = p.value(QStringLiteral("WirelessHardwareEnabled"), true).toBool();
        scheduleRebuild();
    });
    call(m_bus, m_service, QString::fromLatin1(kRoot), QString::fromLatin1(kNm), QStringLiteral("GetDevices"), {}, this,
         [this](const QDBusMessage &r) {
             if (isError(r)) return;
             for (const QString &d : toPaths(r.arguments().value(0))) loadDevice(d);
         });
    loadKnown();
    scheduleRebuild();
}

void NmClient::loadDevice(const QString &path) {
    getAll(m_bus, m_service, path, QString::fromLatin1(kDevice), this, [this, path](const QVariantMap &p, bool ok) {
        if (!ok) return;
        const int type = p.value(QStringLiteral("DeviceType")).toInt();
        const int state = p.value(QStringLiteral("State")).toInt();
        if (type == DevWifi && m_wifiDev.isEmpty()) {
            m_wifiDev = path;
            m_wifiState = state;
            getAll(m_bus, m_service, path, QString::fromLatin1(kWireless), this, [this](const QVariantMap &w, bool ok2) {
                if (!ok2) return;
                m_activeAp = toPaths(w.value(QStringLiteral("ActiveAccessPoint"))).value(0);
                if (m_activeAp == QLatin1String("/")) m_activeAp.clear();
                scheduleRebuild();
            });
            call(m_bus, m_service, path, QString::fromLatin1(kWireless), QStringLiteral("GetAllAccessPoints"), {}, this,
                 [this](const QDBusMessage &r) {
                     if (isError(r)) return;
                     for (const QString &a : toPaths(r.arguments().value(0))) loadAp(a);
                 });
        } else if (type == DevEthernet && m_ethDev.isEmpty()) {
            m_ethDev = path;
            m_ethState = state;
            getAll(m_bus, m_service, path, QString::fromLatin1(kWired), this, [this](const QVariantMap &w, bool ok2) {
                if (!ok2) return;
                m_ethSpeed = w.value(QStringLiteral("Speed")).toInt();
                m_ethCarrier = w.value(QStringLiteral("Carrier")).toBool();
                scheduleRebuild();
            });
        }
        scheduleRebuild();
    });
}

static void fillAp(const QVariantMap &p, QString *ssid, int *strength, int *flags, int *wpa, int *rsn) {
    if (p.contains(QStringLiteral("Ssid"))) *ssid = QString::fromUtf8(toBytes(p.value(QStringLiteral("Ssid"))));
    if (p.contains(QStringLiteral("Strength"))) *strength = p.value(QStringLiteral("Strength")).toInt();
    if (p.contains(QStringLiteral("Flags"))) *flags = p.value(QStringLiteral("Flags")).toInt();
    if (p.contains(QStringLiteral("WpaFlags"))) *wpa = p.value(QStringLiteral("WpaFlags")).toInt();
    if (p.contains(QStringLiteral("RsnFlags"))) *rsn = p.value(QStringLiteral("RsnFlags")).toInt();
}

void NmClient::loadAp(const QString &path) {
    getAll(m_bus, m_service, path, QString::fromLatin1(kAp), this, [this, path](const QVariantMap &p, bool ok) {
        if (!ok) return;
        Ap a;
        a.path = path;
        fillAp(p, &a.ssid, &a.strength, &a.flags, &a.wpa, &a.rsn);
        m_aps.insert(path, a);
        scheduleRebuild();
    });
}

void NmClient::loadKnown() {
    call(m_bus, m_service, QString::fromLatin1(kSettings), QString::fromLatin1(kSettingsIface),
         QStringLiteral("ListConnections"), {}, this, [this](const QDBusMessage &r) {
             if (isError(r)) return;
             const QStringList paths = toPaths(r.arguments().value(0));
             auto acc = QSharedPointer<QHash<QString, QString>>::create();
             auto left = QSharedPointer<int>::create(int(paths.size()));
             if (paths.isEmpty()) { m_known.clear(); scheduleRebuild(); }
             for (const QString &c : paths) {
                 call(m_bus, m_service, c, QString::fromLatin1(kConn), QStringLiteral("GetSettings"), {}, this,
                      [this, c, acc, left](const QDBusMessage &g) {
                          if (!isError(g)) {
                              const DBusSettings s = toSettings(g.arguments().value(0));
                              const QVariantMap w = s.value(QStringLiteral("802-11-wireless"));
                              if (s.value(QStringLiteral("connection")).value(QStringLiteral("type")).toString()
                                      == QLatin1String("802-11-wireless")) {
                                  const QString ssid = QString::fromUtf8(toBytes(w.value(QStringLiteral("ssid"))));
                                  if (!ssid.isEmpty()) acc->insert(ssid, c);
                              }
                          }
                          if (--*left == 0) { m_known = *acc; scheduleRebuild(); }
                      });
             }
         });
}

void NmClient::onSettingsChange(const QDBusMessage &) { loadKnown(); }

void NmClient::onDeviceAdded(const QDBusMessage &m) {
    loadDevice(toPaths(m.arguments().value(0)).value(0));
}

void NmClient::onDeviceRemoved(const QDBusMessage &m) {
    const QString p = toPaths(m.arguments().value(0)).value(0);
    if (p == m_wifiDev) { m_wifiDev.clear(); m_aps.clear(); m_activeAp.clear(); m_wifiState = 0; }
    if (p == m_ethDev) { m_ethDev.clear(); m_ethState = 0; }
    scheduleRebuild();
}

void NmClient::onApAdded(const QDBusMessage &m) {
    if (m.path() != m_wifiDev) return;
    loadAp(toPaths(m.arguments().value(0)).value(0));
}

void NmClient::onApRemoved(const QDBusMessage &m) {
    if (m.path() != m_wifiDev) return;
    m_aps.remove(toPaths(m.arguments().value(0)).value(0));
    scheduleRebuild();
}

void NmClient::onProps(const QDBusMessage &m) {
    const QList<QVariant> a = m.arguments();
    if (a.size() < 2) return;
    const QString iface = a.at(0).toString();
    const QVariantMap p = toMap(a.at(1));
    const QString path = m.path();
    if (path == QLatin1String(kRoot)) {
        if (p.contains(QStringLiteral("WirelessEnabled"))) m_wifiOn = p.value(QStringLiteral("WirelessEnabled")).toBool();
        if (p.contains(QStringLiteral("WirelessHardwareEnabled")))
            m_wifiHw = p.value(QStringLiteral("WirelessHardwareEnabled")).toBool();
        if (!m_wifiOn) { m_aps.clear(); m_activeAp.clear(); }
        else if (m_active) scan();
    } else if (path == m_wifiDev) {
        if (p.contains(QStringLiteral("State"))) m_wifiState = p.value(QStringLiteral("State")).toInt();
        if (iface == QLatin1String(kWireless) && p.contains(QStringLiteral("ActiveAccessPoint"))) {
            m_activeAp = toPaths(p.value(QStringLiteral("ActiveAccessPoint"))).value(0);
            if (m_activeAp == QLatin1String("/")) m_activeAp.clear();
        }
    } else if (path == m_ethDev) {
        if (p.contains(QStringLiteral("State"))) m_ethState = p.value(QStringLiteral("State")).toInt();
        if (p.contains(QStringLiteral("Speed"))) m_ethSpeed = p.value(QStringLiteral("Speed")).toInt();
        if (p.contains(QStringLiteral("Carrier"))) m_ethCarrier = p.value(QStringLiteral("Carrier")).toBool();
    } else if (m_aps.contains(path)) {
        Ap &ap = m_aps[path];
        fillAp(p, &ap.ssid, &ap.strength, &ap.flags, &ap.wpa, &ap.rsn);
    } else {
        return;
    }
    scheduleRebuild();
}

void NmClient::onDeviceState(const QDBusMessage &m) {
    if (m.path() != m_wifiDev) return;
    const QList<QVariant> a = m.arguments();
    if (a.size() < 3) return;
    const uint ns = a.at(0).toUInt(), os = a.at(1).toUInt(), reason = a.at(2).toUInt();
    m_wifiState = int(ns);
    if (!m_attemptSsid.isEmpty()) {
        if (ns == StActivated) {
            const QString s = m_attemptSsid;
            m_attemptSsid.clear(); m_attemptConn.clear(); m_attemptCreated = false;
            m_error.clear();
            emit errorChanged();
            announce(tr("Connected to %1").arg(q(s)));
        } else if (ns == StFailed || (ns == StDisconnected && os >= StPrepare && os <= StSecondaries)) {
            fail(m_attemptSsid, reason);
        }
    }
    scheduleRebuild();
}

void NmClient::fail(QString ssid, uint reason) {
    QString text;
    bool reprompt = false;
    if (authReason(reason)) {
        text = tr("The password for %1 was not accepted.").arg(q(ssid));
        reprompt = true;
    } else if (reason == 53) {
        text = tr("%1 is out of range.").arg(q(ssid));
    } else if (reason == 5 || reason == 6) {
        text = tr("Joined %1 but could not get an address from it.").arg(q(ssid));
    } else {
        text = tr("Could not connect to %1.").arg(q(ssid));
    }
    // A profile that just failed to authenticate is no use; drop it so the next try asks again.
    const QString conn = m_attemptConn.isEmpty() ? m_known.value(ssid) : m_attemptConn;
    if (!conn.isEmpty() && (reprompt || m_attemptCreated))
        call(m_bus, m_service, conn, QString::fromLatin1(kConn), QStringLiteral("Delete"), {}, this, [this](const QDBusMessage &) { loadKnown(); });
    m_attemptSsid.clear(); m_attemptConn.clear(); m_attemptCreated = false;
    setError(text);
    if (reprompt) {
        m_passwordSsid = ssid;
        m_passwordRetry = true;
        emit passwordChanged();
    }
}

void NmClient::setError(const QString &t) {
    m_error = t;
    emit errorChanged();
    if (!t.isEmpty()) announce(t);
    scheduleRebuild();
}

void NmClient::clearError() {
    if (m_error.isEmpty()) return;
    m_error.clear();
    emit errorChanged();
}

// ---- derived state -------------------------------------------------------

QString NmClient::wifiMessage() const {
    if (!m_available) return tr("Wi-Fi is not available on this computer");
    if (!wifiPresent()) return tr("This computer has no Wi-Fi adapter");
    if (!m_wifiHw) return tr("Wi-Fi is turned off by a switch on this computer");
    if (!m_wifiOn) return tr("Wi-Fi is off");
    return {};
}

QString NmClient::kind() const {
    if (!m_available) return {};
    if (m_ethState == StActivated) return QStringLiteral("wired");
    if (m_wifiState == StActivated) return QStringLiteral("wifi");
    return QStringLiteral("offline");
}

QString NmClient::activeSsid() const {
    if (m_wifiState != StActivated) return {};
    return m_aps.value(m_activeAp).ssid;
}

int NmClient::activeStrength() const {
    if (m_wifiState != StActivated) return 0;
    return m_aps.value(m_activeAp).strength;
}

QString NmClient::ethernetText() const {
    if (m_ethDev.isEmpty()) return {};
    if (m_ethState == StActivated) {
        if (m_ethSpeed >= 1000) return tr("Connected, %1 Gbit/s").arg(QLocale().toString(m_ethSpeed / 1000.0, 'g', 3));
        if (m_ethSpeed > 0) return tr("Connected, %1 Mbit/s").arg(m_ethSpeed);
        return tr("Connected");
    }
    if (m_ethState > StDisconnected && m_ethState < StActivated) return tr("Connecting");
    if (m_ethState == StFailed) return tr("Connection failed");
    if (!m_ethCarrier) return tr("Cable unplugged");
    return tr("Not connected");
}

void NmClient::scheduleRebuild() {
    if (!m_rebuild.isActive()) m_rebuild.start();
}

void NmClient::rebuild() {
    QHash<QString, WifiEntry> by;
    const bool wifiUp = m_available && m_wifiOn && !m_wifiDev.isEmpty();
    if (wifiUp) {
        for (const Ap &ap : std::as_const(m_aps)) {
            if (ap.ssid.isEmpty()) continue;
            WifiEntry &e = by[ap.ssid];
            e.ssid = ap.ssid;
            if (ap.strength >= e.strength) { e.strength = ap.strength; e.apPath = ap.path; e.apFlags = ap.flags; e.apWpa = ap.wpa; e.apRsn = ap.rsn; }
            if ((ap.flags & 1) || ap.wpa || ap.rsn) e.secured = true;
            if ((ap.rsn | ap.wpa) & KeyEap && !((ap.rsn | ap.wpa) & (KeyPsk | KeySae))) e.supported = false;
        }
    }
    const QString act = activeSsid();
    for (auto it = by.begin(); it != by.end(); ++it) {
        it->active = it.key() == act;
        it->known = m_known.contains(it.key());
        it->connecting = it.key() == m_attemptSsid
                         || (it.key() == m_aps.value(m_activeAp).ssid && m_wifiState > StDisconnected && m_wifiState < StActivated);
    }
    QList<WifiEntry> rows = by.values();
    std::sort(rows.begin(), rows.end(), [](const WifiEntry &a, const WifiEntry &b) {
        if (a.active != b.active) return a.active;
        // Ten point buckets: a flickering signal does not reshuffle the list under the pointer.
        const int ba = a.strength / 10, bb = b.strength / 10;
        if (ba != bb) return ba > bb;
        return a.ssid.compare(b.ssid, Qt::CaseInsensitive) < 0;
    });
    m_model.setRows(rows);
    emit changed();
}

// ---- actions -------------------------------------------------------------

void NmClient::setActive(bool on) {
    m_active = on;
    if (on) {
        scan();
        m_scanTimer.start();
    } else {
        m_scanTimer.stop();
    }
}

void NmClient::scan() {
    if (m_wifiDev.isEmpty() || !m_wifiOn) return;
    call(m_bus, m_service, m_wifiDev, QString::fromLatin1(kWireless), QStringLiteral("RequestScan"),
         {QVariant(QVariantMap())}, this);  // errors (rate limited, busy) are not worth showing
}

void NmClient::setWifiEnabled(bool on) {
    dbusutil::setProperty(m_bus, m_service, QString::fromLatin1(kRoot), QString::fromLatin1(kNm), QStringLiteral("WirelessEnabled"), on,
                this, [this, on](const QDBusMessage &r) {
                    if (isError(r)) {
                        setError(tr("This account is not allowed to turn Wi-Fi on or off."));
                        return;
                    }
                    m_wifiOn = on;
                    if (!on) { m_aps.clear(); m_activeAp.clear(); }
                    scheduleRebuild();
                    announce(on ? tr("Wi-Fi turned on") : tr("Wi-Fi turned off"));
                });
}

void NmClient::connectTo(const QString &ssid) {
    for (const WifiEntry &e : m_model.rows()) {
        if (e.ssid != ssid) continue;
        if (e.active || e.connecting) return;
        clearError();
        if (!e.supported) {
            setError(tr("%1 needs a work or school sign-in, which this menu cannot do yet.").arg(q(ssid)));
            return;
        }
        if (e.known) {
            m_attemptSsid = ssid;
            m_attemptCreated = false;
            m_attemptConn = m_known.value(ssid);
            announce(tr("Connecting to %1").arg(q(ssid)));
            call(m_bus, m_service, QString::fromLatin1(kRoot), QString::fromLatin1(kNm), QStringLiteral("ActivateConnection"),
                 {QVariant::fromValue(QDBusObjectPath(m_attemptConn)), QVariant::fromValue(QDBusObjectPath(m_wifiDev)),
                  QVariant::fromValue(QDBusObjectPath(e.apPath))},
                 this, [this, ssid](const QDBusMessage &r) {
                     if (isError(r)) { m_attemptSsid.clear(); setError(tr("Could not connect to %1.").arg(q(ssid))); }
                     scheduleRebuild();
                 });
            scheduleRebuild();
        } else if (!e.secured) {
            addAndActivate(ssid, QString(), false, nullptr);
        } else {
            m_passwordSsid = ssid;
            m_passwordRetry = false;
            emit passwordChanged();
        }
        return;
    }
}

void NmClient::cancelPassword() {
    if (m_passwordSsid.isEmpty()) return;
    m_passwordSsid.clear();
    m_passwordRetry = false;
    emit passwordChanged();
}

void NmClient::submitPassword(const QString &ssid, const QString &password) {
    cancelPassword();
    addAndActivate(ssid, password, false, nullptr);
}

void NmClient::connectHidden(const QString &ssid, const QString &password) {
    if (ssid.isEmpty()) return;
    addAndActivate(ssid, password, true, nullptr);
}

void NmClient::addAndActivate(const QString &ssid, const QString &password, bool hidden, const Ap *) {
    // Find what the scan knows about the network to pick the right key type.
    WifiEntry e;
    e.secured = !password.isEmpty();
    for (const WifiEntry &r : m_model.rows())
        if (r.ssid == ssid) e = r;
    QVariantMap conn{{QStringLiteral("id"), ssid},
                     {QStringLiteral("type"), QStringLiteral("802-11-wireless")},
                     {QStringLiteral("uuid"), QUuid::createUuid().toString(QUuid::WithoutBraces)},
                     {QStringLiteral("autoconnect"), true}};
    QVariantMap wifi{{QStringLiteral("ssid"), ssid.toUtf8()}, {QStringLiteral("mode"), QStringLiteral("infrastructure")}};
    if (hidden) wifi.insert(QStringLiteral("hidden"), true);
    DBusSettings s;
    s.insert(QStringLiteral("connection"), conn);
    if (!password.isEmpty()) {
        QVariantMap sec;
        const int keys = e.apWpa | e.apRsn;
        if (!keys && (e.apFlags & 1) && !hidden) {
            sec.insert(QStringLiteral("key-mgmt"), QStringLiteral("none"));
            sec.insert(QStringLiteral("wep-key0"), password);
            sec.insert(QStringLiteral("wep-key-type"), 2u);
        } else {
            const bool saeOnly = (keys & KeySae) && !(keys & KeyPsk);
            sec.insert(QStringLiteral("key-mgmt"), saeOnly ? QStringLiteral("sae") : QStringLiteral("wpa-psk"));
            sec.insert(QStringLiteral("psk"), password);
        }
        wifi.insert(QStringLiteral("security"), QStringLiteral("802-11-wireless-security"));
        s.insert(QStringLiteral("802-11-wireless-security"), sec);
    }
    s.insert(QStringLiteral("802-11-wireless"), wifi);

    m_attemptSsid = ssid;
    m_attemptCreated = true;
    m_attemptConn.clear();
    clearError();
    announce(tr("Connecting to %1").arg(q(ssid)));
    scheduleRebuild();
    const QString ap = e.apPath.isEmpty() ? QStringLiteral("/") : e.apPath;
    call(m_bus, m_service, QString::fromLatin1(kRoot), QString::fromLatin1(kNm),
         QStringLiteral("AddAndActivateConnection"),
         {QVariant::fromValue(s), QVariant::fromValue(QDBusObjectPath(m_wifiDev)), QVariant::fromValue(QDBusObjectPath(ap))},
         this, [this, ssid](const QDBusMessage &r) {
             if (isError(r)) {
                 m_attemptSsid.clear();
                 m_attemptCreated = false;
                 if (r.errorName().contains(QLatin1String("PermissionDenied")) || r.errorName().contains(QLatin1String("AccessDenied")))
                     setError(tr("This account is not allowed to join Wi-Fi networks on this computer."));
                 else
                     setError(tr("Could not connect to %1.").arg(q(ssid)));
                 return;
             }
             m_attemptConn = toPaths(r.arguments().value(0)).value(0);
         });
}

void NmClient::forget(const QString &ssid) {
    const QString c = m_known.value(ssid);
    if (c.isEmpty()) return;
    call(m_bus, m_service, c, QString::fromLatin1(kConn), QStringLiteral("Delete"), {}, this,
         [this, ssid](const QDBusMessage &r) {
             if (isError(r)) setError(tr("This account is not allowed to forget %1.").arg(q(ssid)));
             else announce(tr("Forgot %1").arg(q(ssid)));
             loadKnown();
         });
}

void NmClient::disconnectWifi() {
    if (m_wifiDev.isEmpty()) return;
    call(m_bus, m_service, m_wifiDev, QString::fromLatin1(kDevice), QStringLiteral("Disconnect"), {}, this,
         [this](const QDBusMessage &r) {
             if (!isError(r)) announce(tr("Disconnected from Wi-Fi"));
         });
}
