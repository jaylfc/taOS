#pragma once
#include <QObject>
#include <QString>
#include <QUrl>
#include <QtQml/qqmlregistration.h>

// Environment-driven configuration, exposed to QML as a singleton.
class PocConfig : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON
    Q_PROPERTY(QUrl wallpaper READ wallpaper CONSTANT)
    Q_PROPERTY(bool noBlur READ noBlur CONSTANT)
    Q_PROPERTY(int blurRadius READ blurRadius CONSTANT)
    Q_PROPERTY(int autoCard READ autoCard CONSTANT)
    Q_PROPERTY(int autoScroll READ autoScroll CONSTANT)
    Q_PROPERTY(QString battery READ battery CONSTANT)
public:
    explicit PocConfig(QObject *parent = nullptr);
    QUrl wallpaper() const { return m_wallpaper; }
    bool noBlur() const { return m_noBlur; }
    int blurRadius() const { return m_blurRadius; }
    int autoCard() const { return m_autoCard; }
    int autoScroll() const { return m_autoScroll; }
    QString battery() const { return m_battery; }
private:
    QUrl m_wallpaper;
    bool m_noBlur = false;
    int m_blurRadius = 24;
    int m_autoCard = 0;
    int m_autoScroll = 0;
    QString m_battery;
};
