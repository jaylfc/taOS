#pragma once
#include <QObject>
#include <QString>
#include <QTimer>

// Network: link state from /sys/class/net, no extra dependencies and no
// network traffic. Re-read every 5 s.
class Network : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString kind READ kind NOTIFY changed)          // "wired", "wifi" or "offline"
    Q_PROPERTY(QString interfaceName READ interfaceName NOTIFY changed)
    Q_PROPERTY(QString description READ description NOTIFY changed)
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

    static QString kindName(Kind k);
    // `root` is a prefix for /sys and /proc (empty on a real system).
    static State read(const QString &root);

signals:
    void changed();

private:
    void refresh();
    QString m_root;
    State m_state;
    QTimer m_timer;
};
