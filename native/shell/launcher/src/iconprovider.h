#pragma once
#include <QIcon>
#include <QQuickImageProvider>

namespace taos::launcher {
// image://icon/<percent-encoded theme name or absolute path>
class IconProvider : public QQuickImageProvider {
public:
    IconProvider() : QQuickImageProvider(QQuickImageProvider::Pixmap) {}
    QPixmap requestPixmap(const QString &id, QSize *size, const QSize &requested) override;
};
}
