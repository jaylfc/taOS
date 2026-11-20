#pragma once
#include <QIcon>
#include <QQuickImageProvider>

#include "appinfo.h"

// image://icon/<app_id>: the icon of the app's .desktop entry, drawn from the icon theme.
class IconProvider : public QQuickImageProvider {
public:
    explicit IconProvider(const AppInfo *apps) : QQuickImageProvider(QQuickImageProvider::Pixmap), m_apps(apps) {}
    QPixmap requestPixmap(const QString &id, QSize *size, const QSize &requested) override;
    // Null when the app has no resolvable icon (the overlay then draws a monogram tile).
    static QIcon resolve(const AppInfo *apps, const QString &appId);
private:
    const AppInfo *m_apps;
};
