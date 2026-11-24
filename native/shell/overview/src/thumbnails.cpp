// SPDX-License-Identifier: AGPL-3.0-or-later
#include "thumbnails.h"

#include <QQuickWindow>
#include <QSGSimpleTextureNode>
#include <QSGTexture>
#include <QSet>

static ThumbStore *g_store = nullptr;

void ThumbStore::put(const QString &id, const QImage &img) {
    m_images.insert(id, img);
    m_versions.insert(id, ++m_counter);
    emit updated(id);
}
void ThumbStore::forget(const QString &id) {
    m_images.remove(id);
    m_versions.remove(id);
}
void ThumbStore::retainOnly(const QSet<QString> &ids) {
    for (auto it = m_images.begin(); it != m_images.end();) {
        if (!ids.contains(it.key())) { m_versions.remove(it.key()); it = m_images.erase(it); }
        else ++it;
    }
}

ThumbnailItem::ThumbnailItem(QQuickItem *parent) : QQuickItem(parent) {
    setFlag(ItemHasContents, true);
    if (g_store) connect(g_store, &ThumbStore::updated, this, &ThumbnailItem::onStoreUpdated);
}

void ThumbnailItem::setStore(ThumbStore *s) { g_store = s; }

void ThumbnailItem::setIdentifier(const QString &id) {
    if (id == m_id) return;
    m_id = id;
    emit identifierChanged();
    refreshState();
    update();
}

void ThumbnailItem::refreshState() {
    const QImage img = g_store ? g_store->image(m_id) : QImage();
    const bool has = !img.isNull();
    const QSize sz = has ? img.size() : QSize();
    if (has != m_has || sz != m_imgSize) {
        m_has = has;
        m_imgSize = sz;
        emit hasImageChanged();
    }
}

void ThumbnailItem::onStoreUpdated(const QString &id) {
    if (id != m_id) return;
    refreshState();
    update();
}

QSGNode *ThumbnailItem::updatePaintNode(QSGNode *old, UpdatePaintNodeData *) {
    auto *node = static_cast<QSGSimpleTextureNode *>(old);
    const QImage img = g_store ? g_store->image(m_id) : QImage();
    if (img.isNull() || width() <= 0 || height() <= 0 || !window()) {
        delete node;
        return nullptr;
    }
    if (!node) {
        node = new QSGSimpleTextureNode;
        node->setOwnsTexture(true);
        node->setFiltering(QSGTexture::Linear);
        m_shownVersion = 0;
    }
    const quint64 v = g_store->version(m_id);
    if (v != m_shownVersion || !node->texture()) {
        node->setTexture(window()->createTextureFromImage(img));
        m_shownVersion = v;
    }
    // aspect fit, centred
    const qreal s = std::min(width() / img.width(), height() / img.height());
    const qreal w = img.width() * s, h = img.height() * s;
    node->setRect(QRectF((width() - w) / 2, (height() - h) / 2, w, h));
    return node;
}
