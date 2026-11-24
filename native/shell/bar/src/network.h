#pragma once
#include <QObject>
#include <QString>
#include <QTimer>

class NmClient;

// Network: link state from /sys/class/net, no extra dependencies and no
// network traffic. Re-read every 5 s.
class Network : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString kind READ kind NOTIFY changed)          // "wired", "wifi" or "offline"
    Q_PROPERTY(QString interfaceName READ interfaceName NOTIFY changed)
    Q_PROPERTY(QString description READ description NOTIFY changed)
    Q_PROPERTY(int strength READ strength NOTIFY changed)           // Wi-Fi signal 0..100, 0 when not on Wi-Fi
    Q_PROPERTY(QString ssid READ ssid NOTIFY changed)
    Q_PROPERTY(bool ethernetPresent READ ethernetPresent NOTIFY changed)
    Q_PROPERTY(QString ethernetText READ ethernetText NOTIFY changed)
public:
    enum Kind { Offline, Wired, Wifi };
    Q_ENUM(Kind)

    struct State {
        Kind kind = Offline;
        QString iface;
        bool operator==(const State &o) const { return kind == o.kind && iface == o.iface; }
    };

    explicit Network(const QString &root = QString(), QObject *parent = nullptr);
    QString kind() const { return kindName(m_state.kind); }
    QString interfaceName() const { return m_state.iface; }
    QString description() const;
    int strength() const { return m_nm ? nmStrength() : 0; }
    QString ssid() const;
    bool ethernetPresent() const;
    QString ethernetText() const;

    // When NetworkManager is on the bus its signals drive this object and the
    // /sys poll stops; otherwise the /sys reading is all there is.
    void attachNm(NmClient *nm);
    Q_INVOKABLE void refresh();

    struct Ethernet { bool present = false; QString text; };
    static Ethernet readEthernet(const QString &root);

    static QString kindName(Kind k);
    // `root` is a prefix for /sys and /proc (empty on a real system).
    static State read(const QString &root);

signals:
    void changed();

private:
    int nmStrength() const;
    void nmChanged();
    bool nmDriven() const;
    QString m_root;
    State m_state;
    QTimer m_timer;
    NmClient *m_nm = nullptr;
    Ethernet m_eth;
};
