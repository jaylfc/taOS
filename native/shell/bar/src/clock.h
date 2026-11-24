#pragma once
#include <QDateTime>
#include <QLocale>
#include <QObject>
#include <QTimer>

// Clock: text for the bar's clock and date. Wakes once per minute, on the
// minute boundary (once per second only when showSeconds is on).
class Clock : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString time READ time NOTIFY changed)
    Q_PROPERTY(QString date READ date NOTIFY changed)
    Q_PROPERTY(bool showSeconds READ showSeconds WRITE setShowSeconds NOTIFY settingsChanged)
    Q_PROPERTY(bool use24Hour READ use24Hour WRITE setUse24Hour NOTIFY settingsChanged)
public:
    explicit Clock(QObject *parent = nullptr);

    QString time() const { return m_time; }
    QString date() const { return m_date; }
    bool showSeconds() const { return m_seconds; }
    bool use24Hour() const { return m_24h; }
    void setShowSeconds(bool on);
    void setUse24Hour(bool on);

    // Pure formatting, used by the tests.
    static QString formatTime(const QDateTime &dt, const QLocale &loc, bool h24, bool seconds);
    static QString formatDate(const QDateTime &dt, const QLocale &loc);
    // Milliseconds from `now` to the next minute (or second) boundary, at least 1.
    static int msToNextTick(const QDateTime &now, bool seconds);

signals:
    void changed();
    void settingsChanged();

private:
    void refresh();
    QTimer m_timer;
    QString m_time, m_date;
    bool m_seconds = false;
    bool m_24h = true;
};
