#pragma once
#include <QObject>
#include <QProcess>
#include <QString>
#include <QTimer>

// Volume: best effort through `wpctl get-volume @DEFAULT_AUDIO_SINK@` every
// 5 s and on demand. When wpctl is absent the item is hidden (available == false).
class Volume : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool available READ available NOTIFY changed)
    Q_PROPERTY(int percent READ percent NOTIFY changed)
    Q_PROPERTY(bool muted READ muted NOTIFY changed)
    Q_PROPERTY(QString description READ description NOTIFY changed)
public:
    struct Reading {
        bool ok = false;
        int percent = 0;
        bool muted = false;
    };

    explicit Volume(const QString &wpctl = QString(), QObject *parent = nullptr);
    bool available() const { return m_available; }
    int percent() const { return m_percent; }
    bool muted() const { return m_muted; }
    QString description() const;

    // Parses `Volume: 0.45` or `Volume: 0.45 [MUTED]`.
    static Reading parse(const QString &output);

    Q_INVOKABLE void refresh();

signals:
    void changed();

private:
    QString m_wpctl;
    bool m_available = false;
    int m_percent = 0;
    bool m_muted = false;
    QProcess m_proc;
    QTimer m_timer;
};
