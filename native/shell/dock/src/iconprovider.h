#pragma once
#include <QQuickImageProvider>

// image://dockicon/<name or absolute path>: the icon theme's icon, or an empty image when there is
// none (the QML then draws the bundled fallback glyph).
class DockIconProvider : public QQuickImageProvider {
public:
    DockIconProvider() : QQuickImageProvider(QQuickImageProvider::Pixmap) {}
    QPixmap requestPixmap(const QString &id, QSize *size, const QSize &requested) override;
};
