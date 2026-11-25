// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The map surface: paints the styled tiles of the open map pack, the markers and the person's own position with
// QPainter. It has no network and no web view. Every colour and size comes from the QML side (the theme tokens).
#include <QColor>
#include <QPointer>
#include <QQuickPaintedItem>
#include <QVariantAnimation>
#include <QVariantList>
#include <QVariantMap>

#include "backend.h"

namespace Taos::Survival {

class MapView : public QQuickPaintedItem
{
    Q_OBJECT
    Q_PROPERTY(QObject *engine READ engine WRITE setEngine NOTIFY engineChanged)
    Q_PROPERTY(double centerLon READ centerLon WRITE setCenterLon NOTIFY viewChanged)
    Q_PROPERTY(double centerLat READ centerLat WRITE setCenterLat NOTIFY viewChanged)
    Q_PROPERTY(double zoom READ zoom WRITE setZoom NOTIFY viewChanged)
    Q_PROPERTY(QVariantMap mapPalette READ mapPalette WRITE setMapPalette NOTIFY paletteChanged)
    Q_PROPERTY(QString fontFamily READ fontFamily WRITE setFontFamily NOTIFY paletteChanged)
    Q_PROPERTY(int labelSize READ labelSize WRITE setLabelSize NOTIFY paletteChanged)
    Q_PROPERTY(QString selectedMarker READ selectedMarker WRITE setSelectedMarker NOTIFY selectionChanged)
    Q_PROPERTY(bool showMarkers READ showMarkers WRITE setShowMarkers NOTIFY paletteChanged)
    Q_PROPERTY(bool reduceMotion READ reduceMotion WRITE setReduceMotion NOTIFY paletteChanged)
    Q_PROPERTY(double metersPerPixel READ metersPerPixel NOTIFY viewChanged)
public:
    explicit MapView(QQuickItem *parent = nullptr);
    void paint(QPainter *painter) override;
    QObject *engine() const { return m_engine; }
    void setEngine(QObject *e);
    double centerLon() const { return m_lon; }
    double centerLat() const { return m_lat; }
    double zoom() const { return m_zoom; }
    void setCenterLon(double v);
    void setCenterLat(double v);
    void setZoom(double v);
    QVariantMap mapPalette() const { return m_paletteMap; }
    void setMapPalette(const QVariantMap &m);
    QString fontFamily() const { return m_family; }
    void setFontFamily(const QString &f) { m_family = f; update(); Q_EMIT paletteChanged(); }
    int labelSize() const { return m_labelSize; }
    void setLabelSize(int s) { m_labelSize = s; update(); Q_EMIT paletteChanged(); }
    QString selectedMarker() const { return m_selected; }
    void setSelectedMarker(const QString &id) { if (id != m_selected) { m_selected = id; update(); Q_EMIT selectionChanged(); } }
    bool showMarkers() const { return m_showMarkers; }
    bool reduceMotion() const { return m_reduceMotion; }
    void setReduceMotion(bool b) { m_reduceMotion = b; Q_EMIT paletteChanged(); }
    void setShowMarkers(bool b) { m_showMarkers = b; update(); Q_EMIT paletteChanged(); }
    double metersPerPixel() const;

    Q_INVOKABLE void panBy(double dx, double dy);
    Q_INVOKABLE void zoomAt(double x, double y, double delta);
    Q_INVOKABLE QVariantMap lonLatAt(double x, double y) const;
    Q_INVOKABLE QVariantMap screenPos(double lon, double lat) const;
    Q_INVOKABLE QString markerAt(double x, double y) const;
    Q_INVOKABLE void flyTo(double lon, double lat, double zoom);
    Q_INVOKABLE void fitBounds(double west, double south, double east, double north);
    Q_INVOKABLE void resetView();
    Q_INVOKABLE void setView(double lon, double lat, double zoom);
Q_SIGNALS:
    void engineChanged();
    void viewChanged();
    void paletteChanged();
    void selectionChanged();

protected:
    void geometryChange(const QRectF &n, const QRectF &o) override;

private:
    void clampView();
    void onEngineLoaded();
    double minZoom() const;
    double maxZoom() const;
    QColor col(const char *key, const QColor &fallback) const;
    QPointer<MapEngine> m_engine;
    double m_lon = 0, m_lat = 0, m_zoom = 1;
    QVariantMap m_paletteMap;
    QString m_family;
    int m_labelSize = 12;
    QString m_selected;
    bool m_userMoved = false;
    bool m_showMarkers = true;
    bool m_reduceMotion = false;
    QVariantAnimation m_fly;
    double m_flyFromLon = 0, m_flyFromLat = 0, m_flyFromZoom = 0, m_flyToLon = 0, m_flyToLat = 0, m_flyToZoom = 0;
};

} // namespace Taos::Survival
