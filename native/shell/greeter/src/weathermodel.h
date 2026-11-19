#pragma once
#include <QDateTime>
#include <QObject>
#include <QTimer>

// WeatherModel: current conditions read from the local cache file written by
// taos-weather-cache. The greeter never touches the network: no file, a stale
// file (over 3 hours old) or a malformed file all mean "no weather".
class WeatherModel : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool available READ available NOTIFY changed)
    Q_PROPERTY(QString temperature READ temperature NOTIFY changed)  // "14°"
    Q_PROPERTY(QString condition READ condition NOTIFY changed)
    Q_PROPERTY(QString place READ place NOTIFY changed)
    Q_PROPERTY(QString icon READ icon NOTIFY changed)  // icon id, see WeatherIcon.qml
public:
    struct Reading {
        bool valid = false;
        QString place, condition, icon, temperature;
        int code = -1;
        bool isDay = true;
        QDateTime updated;
    };
    static constexpr int kMaxAgeSecs = 3 * 3600;

    explicit WeatherModel(QObject *parent = nullptr);
    // Parse a cache file's bytes. Returns an invalid Reading for anything that
    // is malformed, incomplete or older than kMaxAgeSecs at `now`.
    static Reading parse(const QByteArray &json, const QDateTime &now);
    // WMO code -> icon id ("clear", "partly-cloudy", "cloudy", "fog", "drizzle",
    // "rain", "snow", "thunder"; "" for an unknown code) and default text.
    static QString iconForCode(int code);
    static QString textForCode(int code);

    void setPath(const QString &p) { m_path = p; }
    static QString defaultPath();
    bool available() const { return m_r.valid; }
    QString temperature() const { return m_r.temperature; }
    QString condition() const { return m_r.condition; }
    QString place() const { return m_r.place; }
    // Icon id including day/night variant where it matters: clear-day, clear-night, ...
    QString icon() const;
public slots:
    void refresh();
signals:
    void changed();

private:
    QString m_path;
    Reading m_r;
    QTimer m_timer;
};
