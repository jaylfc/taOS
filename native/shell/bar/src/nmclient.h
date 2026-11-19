#pragma once
#include <QAbstractListModel>
#include <QDBusConnection>
#include <QDBusMessage>
#include <QDBusObjectPath>
#include <QDBusServiceWatcher>
#include <QHash>
#include <QTimer>
#include <QVariantMap>

// One row of the Wi-Fi list: every access point that shares an SSID is folded
// into one network.
struct WifiEntry {
    QString ssid;
    int strength = 0;     // 0..100
    bool secured = false;
    bool supported = true;  // false: needs a sign-in this menu cannot do (802.1X)
    bool active = false;
    bool known = false;   // a saved profile exists
    bool connecting = false;
    QString apPath;       // strongest access point
    int apFlags = 0, apWpa = 0, apRsn = 0;
};

class WifiModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Roles { SsidRole = Qt::UserRole + 1, StrengthRole, SecuredRole, ActiveRole, KnownRole, ConnectingRole, SupportedRole, DescriptionRole };
    explicit WifiModel(QObject *parent = nullptr) : QAbstractListModel(parent) {}
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : int(m_rows.size()); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    const QList<WifiEntry> &rows() const { return m_rows; }
    void setRows(const QList<WifiEntry> &rows);
    static QString describe(const WifiEntry &e);
    static QString strengthWord(int s);
private:
    QList<WifiEntry> m_rows;
};

// NetworkManager over D-Bus: Wi-Fi list and connect flow, Ethernet state.
// Signal driven: nothing runs while nothing changes. `bus` is the system bus
// in the bar and a private bus in the tests.
class NmClient : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool available READ available NOTIFY changed)
    Q_PROPERTY(bool switchVisible READ switchVisible NOTIFY changed)
    Q_PROPERTY(bool wifiEnabled READ wifiEnabled NOTIFY changed)
    Q_PROPERTY(QString wifiMessage READ wifiMessage NOTIFY changed)
    Q_PROPERTY(WifiModel *networks READ networks CONSTANT)
    Q_PROPERTY(QString kind READ kind NOTIFY changed)
    Q_PROPERTY(QString activeSsid READ activeSsid NOTIFY changed)
    Q_PROPERTY(int activeStrength READ activeStrength NOTIFY changed)
    Q_PROPERTY(bool ethernetPresent READ ethernetPresent NOTIFY changed)
    Q_PROPERTY(QString ethernetText READ ethernetText NOTIFY changed)
    Q_PROPERTY(QString passwordSsid READ passwordSsid NOTIFY passwordChanged)
    Q_PROPERTY(bool passwordRetry READ passwordRetry NOTIFY passwordChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorChanged)
    Q_PROPERTY(QString connectingSsid READ connectingSsid NOTIFY changed)
public:
    explicit NmClient(const QDBusConnection &bus, QObject *parent = nullptr);
    void start();  // subscribe and read the initial state

    bool available() const { return m_available; }
    bool wifiPresent() const { return !m_wifiDev.isEmpty(); }
    bool switchVisible() const { return m_available && wifiPresent() && m_wifiHw; }
    bool wifiEnabled() const { return m_wifiOn; }
    QString wifiMessage() const;
    WifiModel *networks() { return &m_model; }
    QString kind() const;  // "wired", "wifi", "offline"; empty when NetworkManager is absent
    QString activeSsid() const;
    int activeStrength() const;
    bool ethernetPresent() const { return !m_ethDev.isEmpty(); }
    QString ethernetText() const;
    QString passwordSsid() const { return m_passwordSsid; }
    bool passwordRetry() const { return m_passwordRetry; }
    QString errorText() const { return m_error; }
    QString connectingSsid() const { return m_attemptSsid; }

    // The menu is open: scan now, and keep scan results fresh while it is.
    Q_INVOKABLE void setActive(bool on);
    Q_INVOKABLE void scan();
    Q_INVOKABLE void setWifiEnabled(bool on);
    Q_INVOKABLE void connectTo(const QString &ssid);                       // saved: activate; open: join; secured: asks for a password
    Q_INVOKABLE void submitPassword(const QString &ssid, const QString &password);
    Q_INVOKABLE void cancelPassword();
    Q_INVOKABLE void connectHidden(const QString &ssid, const QString &password);
    Q_INVOKABLE void forget(const QString &ssid);
    Q_INVOKABLE void disconnectWifi();
    Q_INVOKABLE void clearError();

signals:
    void changed();
    void passwordChanged();
    void errorChanged();
    void announcement(const QString &text);

private slots:
    void onProps(const QDBusMessage &m);
    void onDeviceAdded(const QDBusMessage &m);
    void onDeviceRemoved(const QDBusMessage &m);
    void onApAdded(const QDBusMessage &m);
    void onApRemoved(const QDBusMessage &m);
    void onDeviceState(const QDBusMessage &m);
    void onSettingsChange(const QDBusMessage &m);

private:
    struct Ap { QString path, ssid; int strength = 0, flags = 0, wpa = 0, rsn = 0; };
    void refreshAll();
    void clearAll();
    void loadDevice(const QString &path);
    void loadAp(const QString &path);
    void loadKnown();
    void scheduleRebuild();
    void rebuild();
    void fail(const QString &ssid, uint reason);
    void addAndActivate(const QString &ssid, const QString &password, bool hidden, const Ap *ap);
    void setError(const QString &t);
    void announce(const QString &t) { emit announcement(t); }
    QString service() const { return m_service; }

    QDBusConnection m_bus;
    QString m_service = QStringLiteral("org.freedesktop.NetworkManager");
    QDBusServiceWatcher m_watcher;
    bool m_available = false, m_wifiOn = false, m_wifiHw = true, m_active = false;
    QString m_wifiDev, m_ethDev;
    int m_wifiState = 0, m_ethState = 0, m_ethSpeed = 0;
    bool m_ethCarrier = false;
    QString m_activeAp;
    QHash<QString, Ap> m_aps;
    QHash<QString, QString> m_known;  // ssid -> saved connection path
    WifiModel m_model;
    QTimer m_rebuild;
    QTimer m_scanTimer;
    // The attempt in flight.
    QString m_attemptSsid, m_attemptConn;
    bool m_attemptCreated = false;
    QString m_passwordSsid;
    bool m_passwordRetry = false;
    QString m_error;
};
