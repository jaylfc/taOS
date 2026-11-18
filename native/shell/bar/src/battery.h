#pragma once
#include <QObject>
#include <QString>
#include <QTimer>

// Battery: reads /sys/class/power_supply/BAT*. Hidden (present == false) when
// there is no battery. Refreshed every 30 s.
class Battery : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool present READ present NOTIFY changed)
    Q_PROPERTY(int percent READ percent NOTIFY changed)
    Q_PROPERTY(bool charging READ charging NOTIFY changed)
    Q_PROPERTY(QString description READ description NOTIFY changed)
public:
    struct Reading {
        bool present = false;
        int percent = 0;
        bool charging = false;
        QString status;
        bool operator==(const Reading &o) const {
            return present == o.present && percent == o.percent && charging == o.charging
                   && status == o.status;
        }
    };

    explicit Battery(const QString &root = QString(), QObject *parent = nullptr);
    bool present() const { return m_r.present; }
    int percent() const { return m_r.percent; }
    bool charging() const { return m_r.charging; }
    QString description() const;

    // `root` is a prefix for /sys (empty on a real system), so a test can feed a fake tree.
    static Reading read(const QString &root);

signals:
    void changed();

private:
    void refresh();
    QString m_root;
    Reading m_r;
    QTimer m_timer;
};
