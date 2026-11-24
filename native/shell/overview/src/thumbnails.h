// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QHash>
#include <QImage>
#include <QObject>
#include <QQuickItem>
#include <QString>

// The newest thumbnail per window identifier. Lives on the GUI thread; only thumbnail-size images
// are kept (the capture thread has already scaled them), and they stay after the overlay hides so
// the next show can paint something at once.
class ThumbStore : public QObject {
    Q_OBJECT
public:
    explicit ThumbStore(QObject *parent = nullptr) : QObject(parent) {}
    QImage image(const QString &id) const { return m_images.value(id); }
    quint64 version(const QString &id) const { return m_versions.value(id); }
    void put(const QString &id, const QImage &img);
    void forget(const QString &id);
    void retainOnly(const QSet<QString> &ids);
    int count() const { return int(m_images.size()); }
signals:
    void updated(const QString &id);
private:
    QHash<QString, QImage> m_images;
    QHash<QString, quint64> m_versions;
    quint64 m_counter = 0;
};

// Paints the thumbnail of one window as a texture, aspect-fit and centred.
class ThumbnailItem : public QQuickItem {
    Q_OBJECT
    Q_PROPERTY(QString identifier READ identifier WRITE setIdentifier NOTIFY identifierChanged)
    Q_PROPERTY(bool hasImage READ hasImage NOTIFY hasImageChanged)
    Q_PROPERTY(QSize imageSize READ imageSize NOTIFY hasImageChanged)
public:
    explicit ThumbnailItem(QQuickItem *parent = nullptr);
    static void setStore(ThumbStore *s);
    QString identifier() const { return m_id; }
    void setIdentifier(const QString &id);
    bool hasImage() const { return m_has; }
    QSize imageSize() const { return m_imgSize; }
signals:
    void identifierChanged();
    void hasImageChanged();
protected:
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *) override;
private:
    void onStoreUpdated(const QString &id);
    void refreshState();
    QString m_id;
    bool m_has = false;
    QSize m_imgSize;
    quint64 m_shownVersion = 0;
};
