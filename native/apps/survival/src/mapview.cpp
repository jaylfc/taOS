// SPDX-License-Identifier: AGPL-3.0-or-later
#include "mapview.h"

#include <QFont>
#include <QFontMetricsF>
#include <QPainter>
#include <QPainterPath>
#include <QSet>
#include <algorithm>
#include <cmath>

namespace Taos::Survival {

using namespace Qt::StringLiterals;

namespace {
// metres per pixel at the equator for a 512 px world at zoom 0
constexpr double kEquator = 40075016.686;
}

MapView::MapView(QQuickItem *parent) : QQuickPaintedItem(parent)
{
    setAntialiasing(true);
    setAcceptedMouseButtons(Qt::NoButton);   // the QML around it handles pointer and keys
    setRenderTarget(QQuickPaintedItem::FramebufferObject);
    m_fly.setDuration(260);
    m_fly.setEasingCurve(QEasingCurve::OutCubic);
    m_fly.setStartValue(0.0);
    m_fly.setEndValue(1.0);
    connect(&m_fly, &QVariantAnimation::valueChanged, this, [this](const QVariant &v) {
        const double t = v.toDouble();
        m_lon = m_flyFromLon + (m_flyToLon - m_flyFromLon) * t;
        m_lat = m_flyFromLat + (m_flyToLat - m_flyFromLat) * t;
        m_zoom = m_flyFromZoom + (m_flyToZoom - m_flyFromZoom) * t;
        update();
        Q_EMIT viewChanged();
    });
}

void MapView::setEngine(QObject *e)
{
    auto *me = qobject_cast<MapEngine *>(e);
    if (me == m_engine)
        return;
    if (m_engine)
        disconnect(m_engine, nullptr, this, nullptr);
    m_engine = me;
    if (m_engine) {
        connect(m_engine, &MapEngine::loadedChanged, this, &MapView::onEngineLoaded);
        connect(m_engine, &MapEngine::markersChanged, this, [this] { update(); });
        connect(m_engine, &MapEngine::meChanged, this, [this] { update(); });
        if (m_engine->loaded())
            onEngineLoaded();
    }
    Q_EMIT engineChanged();
    update();
}

void MapView::onEngineLoaded()
{
    if (m_engine && m_engine->loaded()) {
        m_userMoved = false;
        resetView();
    }
    update();
}

double MapView::minZoom() const
{
    const QVariantMap v = m_engine ? m_engine->view() : QVariantMap();
    return std::max(0.0, v.value(u"minZoom"_s, 0).toDouble());
}
double MapView::maxZoom() const
{
    const QVariantMap v = m_engine ? m_engine->view() : QVariantMap();
    return v.value(u"maxZoom"_s, 12).toDouble() + 2.5;   // a little past the pack's own detail
}

void MapView::clampView()
{
    m_zoom = std::clamp(m_zoom, minZoom(), maxZoom());
    m_lat = std::clamp(m_lat, -85.0, 85.0);
    while (m_lon > 180) m_lon -= 360;
    while (m_lon < -180) m_lon += 360;
}

void MapView::setCenterLon(double v) { m_userMoved = true; m_fly.stop(); m_lon = v; clampView(); update(); Q_EMIT viewChanged(); }
void MapView::setCenterLat(double v) { m_userMoved = true; m_fly.stop(); m_lat = v; clampView(); update(); Q_EMIT viewChanged(); }
void MapView::setZoom(double v) { m_userMoved = true; m_fly.stop(); m_zoom = v; clampView(); update(); Q_EMIT viewChanged(); }
void MapView::setMapPalette(const QVariantMap &m) { m_paletteMap = m; update(); Q_EMIT paletteChanged(); }
double MapView::metersPerPixel() const { return std::cos(m_lat * M_PI / 180.0) * kEquator / (512.0 * std::pow(2.0, m_zoom)); }
void MapView::geometryChange(const QRectF &n, const QRectF &o)
{
    QQuickPaintedItem::geometryChange(n, o);
    // until the person moves the map, the whole pack stays fitted to whatever size the window settles at
    if (!m_userMoved && m_engine && m_engine->loaded() && n.width() > 10 && n.height() > 10)
        resetView();
    update();
}

QColor MapView::col(const char *key, const QColor &fallback) const
{
    const QVariant v = m_paletteMap.value(QLatin1String(key));
    return v.isValid() ? v.value<QColor>() : fallback;
}

void MapView::panBy(double dx, double dy)
{
    m_userMoved = true;
    m_fly.stop();
    const double cx = lonToWorldX(m_lon, m_zoom) - dx, cy = latToWorldY(m_lat, m_zoom) - dy;
    m_lon = worldXToLon(cx, m_zoom);
    m_lat = worldYToLat(cy, m_zoom);
    clampView();
    update();
    Q_EMIT viewChanged();
}

void MapView::zoomAt(double x, double y, double delta)
{
    m_userMoved = true;
    m_fly.stop();
    const double z = std::clamp(m_zoom + delta, minZoom(), maxZoom());
    if (z == m_zoom)
        return;
    // keep the point under the pointer where it is
    const QVariantMap under = lonLatAt(x, y);
    m_zoom = z;
    const double ux = lonToWorldX(under.value(u"lon"_s).toDouble(), z), uy = latToWorldY(under.value(u"lat"_s).toDouble(), z);
    const double cx = ux - (x - width() / 2), cy = uy - (y - height() / 2);
    m_lon = worldXToLon(cx, z);
    m_lat = worldYToLat(cy, z);
    clampView();
    update();
    Q_EMIT viewChanged();
}

QVariantMap MapView::lonLatAt(double x, double y) const
{
    const double wx = lonToWorldX(m_lon, m_zoom) + (x - width() / 2), wy = latToWorldY(m_lat, m_zoom) + (y - height() / 2);
    return {{u"lon"_s, worldXToLon(wx, m_zoom)}, {u"lat"_s, worldYToLat(wy, m_zoom)}};
}

QVariantMap MapView::screenPos(double lon, double lat) const
{
    return {{u"x"_s, lonToWorldX(lon, m_zoom) - lonToWorldX(m_lon, m_zoom) + width() / 2},
            {u"y"_s, latToWorldY(lat, m_zoom) - latToWorldY(m_lat, m_zoom) + height() / 2}};
}

QString MapView::markerAt(double x, double y) const
{
    if (!m_engine || !m_showMarkers)
        return {};
    QString best;
    double bestD = 18 * 18;
    for (const QVariant &v : m_engine->markers()) {
        const QVariantMap m = v.toMap();
        const QVariantMap p = screenPos(m.value(u"lon"_s).toDouble(), m.value(u"lat"_s).toDouble());
        // the pin head sits above its point
        const double dx = p.value(u"x"_s).toDouble() - x, dy = p.value(u"y"_s).toDouble() - 12 - y;
        const double d = dx * dx + dy * dy;
        if (d < bestD) { bestD = d; best = m.value(u"id"_s).toString(); }
    }
    return best;
}

void MapView::flyTo(double lon, double lat, double zoom)
{
    m_userMoved = true;
    m_flyFromLon = m_lon; m_flyFromLat = m_lat; m_flyFromZoom = m_zoom;
    m_flyToLon = lon; m_flyToLat = std::clamp(lat, -85.0, 85.0); m_flyToZoom = std::clamp(zoom, minZoom(), maxZoom());
    m_fly.stop();
    if (m_reduceMotion) {   // the person asked for reduced motion: jump, do not glide
        m_lon = m_flyToLon; m_lat = m_flyToLat; m_zoom = m_flyToZoom;
        update();
        Q_EMIT viewChanged();
        return;
    }
    m_fly.start();
}

void MapView::fitBounds(double west, double south, double east, double north)
{
    const double w = std::max(width(), 200.0), h = std::max(height(), 200.0);
    const double zx = std::log2(w / ((east - west) / 360.0 * 512.0));
    const double yspan = latToWorldY(south, 0) - latToWorldY(north, 0);
    const double zy = std::log2(h / std::max(yspan, 1e-6));
    m_fly.stop();
    m_zoom = std::clamp(std::min(zx, zy) - 0.15, minZoom(), maxZoom());
    m_lon = (west + east) / 2;
    m_lat = worldYToLat((latToWorldY(south, m_zoom) + latToWorldY(north, m_zoom)) / 2, m_zoom);
    update();
    Q_EMIT viewChanged();
}

void MapView::setView(double lon, double lat, double zoom)
{
    m_userMoved = true;
    m_fly.stop();
    m_lon = lon;
    m_lat = lat;
    m_zoom = zoom;
    clampView();
    update();
    Q_EMIT viewChanged();
}

void MapView::resetView()
{
    if (!m_engine || !m_engine->loaded())
        return;
    m_userMoved = false;
    const QVariantMap v = m_engine->view();
    fitBounds(v.value(u"west"_s).toDouble(), v.value(u"south"_s).toDouble(), v.value(u"east"_s).toDouble(), v.value(u"north"_s).toDouble());
}

void MapView::paint(QPainter *p)
{
    const QColor ground = col("ground", Qt::gray), water = col("water", Qt::blue), earth = col("earth", Qt::lightGray);
    const QColor urban = col("urban", earth), green = col("green", earth), building = col("building", earth);
    const QColor text = col("text", Qt::black), halo = col("halo", Qt::white), accent = col("accent", Qt::red);
    const QColor onAccent = col("onAccent", Qt::white);
    p->setRenderHint(QPainter::Antialiasing, true);
    p->setRenderHint(QPainter::TextAntialiasing, true);
    p->fillRect(QRectF(0, 0, width(), height()), ground);
    MapData *data = m_engine ? m_engine->data() : nullptr;
    if (!data)
        return;

    const PmHeader &h = data->archive().header();
    const int tz = int(std::clamp(std::floor(m_zoom + 0.0001), double(h.minZoom), double(h.maxZoom)));
    const double T = 512.0 * std::pow(2.0, m_zoom - tz);   // a tile's size on screen
    const double cx = lonToWorldX(m_lon, tz) * std::pow(2.0, m_zoom - tz), cy = latToWorldY(m_lat, tz) * std::pow(2.0, m_zoom - tz);
    const double left = cx - width() / 2, top = cy - height() / 2;
    const int n = 1 << tz;
    const int x0 = int(std::floor(left / T)), x1 = int(std::floor((left + width()) / T));
    const int y0 = std::max(0, int(std::floor(top / T))), y1 = std::min(n - 1, int(std::floor((top + height()) / T)));

    // line widths grow with the zoom, in device pixels
    const double zf = std::clamp((m_zoom - 4.0) / 10.0, 0.0, 1.0);
    auto lw = [&](double a, double b) { return a + (b - a) * zf; };
    struct LineStyle { QColor color; double w; bool dashed; };
    const QColor roadMinor = col("roadMinor", text), roadMedium = col("roadMedium", text), roadMajor = col("roadMajor", text);
    const QColor roadHighway = col("roadHighway", text), path = col("path", text), rail = col("rail", text), border = col("border", text);
    const LineStyle lines[LineCount] = {
        /*LineWater*/ {water, lw(0.6, 2.2), false}, /*LinePath*/ {path, lw(0.4, 1.0), true}, /*LineMinor*/ {roadMinor, lw(0.4, 2.6), false},
        /*LineMedium*/ {roadMedium, lw(0.7, 3.4), false}, /*LineMajor*/ {roadMajor, lw(1.0, 4.4), false},
        /*LineHighway*/ {roadHighway, lw(1.4, 5.6), false}, /*LineRail*/ {rail, lw(0.5, 1.2), true},
        /*LineBorderCountry*/ {border, lw(1.0, 1.6), true}, /*LineBorderRegion*/ {border, lw(0.5, 0.9), true}};
    const double minRoadZoom[LineCount] = {0, 12.0, 11.0, 9.5, 8.0, 6.5, 9.0, 0, 5.0};

    struct Placed { MapLabel l; QPointF pos; };
    QVector<Placed> labels;

    for (int tx = x0; tx <= x1; ++tx) {
        const int wx = ((tx % n) + n) % n;
        for (int ty = y0; ty <= y1; ++ty) {
            const auto tile = data->tile(tz, wx, ty);
            if (!tile || !tile->hasData)
                continue;
            const QPointF origin(tx * T - left, ty * T - top);
            const double s = T / tile->extent;
            p->save();
            p->setClipRect(QRectF(origin, QSizeF(T, T)).adjusted(-0.5, -0.5, 0.5, 0.5));
            p->fillRect(QRectF(origin, QSizeF(T, T)).adjusted(-0.5, -0.5, 0.5, 0.5), water);
            p->translate(origin);
            p->scale(s, s);
            p->setPen(Qt::NoPen);
            const QColor fills[FillCount] = {earth, urban, green, water, building};
            const double fillMin[FillCount] = {0, 8.0, 5.0, 0, 13.0};
            for (int f = 0; f < FillCount; ++f) {
                if (m_zoom < fillMin[f] || tile->fills[f].isEmpty())
                    continue;
                p->setBrush(fills[f]);
                p->drawPath(tile->fills[f]);
            }
            p->setBrush(Qt::NoBrush);
            for (int l = 0; l < LineCount; ++l) {
                if (m_zoom < minRoadZoom[l] || tile->lines[l].isEmpty())
                    continue;
                QPen pen(lines[l].color, lines[l].w, lines[l].dashed ? Qt::CustomDashLine : Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin);
                pen.setCosmetic(true);
                if (lines[l].dashed) pen.setDashPattern({4.0, 3.0});
                p->setPen(pen);
                p->drawPath(tile->lines[l]);
            }
            p->restore();
            for (const MapLabel &lab : tile->labels)
                if (m_zoom >= lab.minZoom)
                    labels.append({lab, origin + QPointF(lab.pos.x() * s, lab.pos.y() * s)});
        }
    }

    // place names: most important first, none over another
    std::sort(labels.begin(), labels.end(), [](const Placed &a, const Placed &b) {
        return a.l.cls != b.l.cls ? a.l.cls < b.l.cls : a.l.rank < b.l.rank; });
    QFont base(m_family);
    base.setPixelSize(m_labelSize);
    QList<QRectF> taken;
    QSet<QString> shown;
    for (const Placed &pl : std::as_const(labels)) {
        if (pl.pos.x() < -50 || pl.pos.y() < -20 || pl.pos.x() > width() + 50 || pl.pos.y() > height() + 20)
            continue;
        if (shown.contains(pl.l.text))
            continue;
        QFont f = base;
        f.setPixelSize(pl.l.cls <= LabelCity ? m_labelSize + 2 : pl.l.cls == LabelTown ? m_labelSize + 1 : m_labelSize);
        f.setWeight(pl.l.cls <= LabelCity ? QFont::DemiBold : QFont::Medium);
        if (pl.l.cls == LabelCountry) { f.setCapitalization(QFont::AllUppercase); f.setLetterSpacing(QFont::AbsoluteSpacing, 2); }
        const QFontMetricsF fm(f);
        const QString text_ = pl.l.text;
        const QRectF r(pl.pos.x() - fm.horizontalAdvance(text_) / 2, pl.pos.y() - fm.height() / 2, fm.horizontalAdvance(text_), fm.height());
        const QRectF pad = r.adjusted(-4, -2, 4, 2);
        bool clash = false;
        for (const QRectF &t : std::as_const(taken))
            if (t.intersects(pad)) { clash = true; break; }
        if (clash)
            continue;
        taken.append(pad);
        shown.insert(pl.l.text);
        QPainterPath tp;
        tp.addText(r.left(), r.top() + fm.ascent(), f, text_);
        p->setPen(QPen(halo, 3.2, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
        p->setBrush(Qt::NoBrush);
        p->drawPath(tp);
        p->setPen(Qt::NoPen);
        p->setBrush(pl.l.cls <= LabelRegion ? col("textSecondary", text) : text);
        p->drawPath(tp);
        if (pl.l.cls == LabelCity) {   // a dot beside the big names
            p->setBrush(text);
            p->drawEllipse(QPointF(pl.pos.x() - fm.horizontalAdvance(text_) / 2 - 7, pl.pos.y()), 2.2, 2.2);
        }
    }

    if (!m_engine)
        return;
    // the person's markers
    if (m_showMarkers) {
        QFont mf = base;
        mf.setWeight(QFont::DemiBold);
        const QFontMetricsF fm(mf);
        for (const QVariant &v : m_engine->markers()) {
            const QVariantMap m = v.toMap();
            const QVariantMap sp = screenPos(m.value(u"lon"_s).toDouble(), m.value(u"lat"_s).toDouble());
            const QPointF pt(sp.value(u"x"_s).toDouble(), sp.value(u"y"_s).toDouble());
            if (pt.x() < -30 || pt.y() < -40 || pt.x() > width() + 30 || pt.y() > height() + 30)
                continue;
            const bool sel = m.value(u"id"_s).toString() == m_selected;
            const double r = sel ? 10 : 8;
            QPainterPath pin;
            pin.moveTo(pt);
            pin.cubicTo(pt + QPointF(-r * 0.2, -r * 0.9), pt + QPointF(-r, -r * 1.2), pt + QPointF(-r, -r * 1.9));
            pin.arcTo(QRectF(pt.x() - r, pt.y() - r * 2.9, r * 2, r * 2), 180, -180);
            pin.cubicTo(pt + QPointF(r, -r * 1.2), pt + QPointF(r * 0.2, -r * 0.9), pt);
            p->setPen(QPen(halo, 1.5));
            p->setBrush(accent);
            p->drawPath(pin);
            p->setPen(Qt::NoPen);
            p->setBrush(onAccent);
            p->drawEllipse(pt + QPointF(0, -r * 1.9), r * 0.38, r * 0.38);
            const QString name = m.value(u"name"_s).toString();
            if (!name.isEmpty() && (sel || m_zoom >= 7)) {
                QPainterPath tp;
                tp.addText(pt.x() + r + 4, pt.y() - r * 1.9 + fm.ascent() / 2 - 1, mf, name);
                p->setPen(QPen(halo, 3.2, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
                p->setBrush(Qt::NoBrush);
                p->drawPath(tp);
                p->setPen(Qt::NoPen);
                p->setBrush(text);
                p->drawPath(tp);
            }
        }
    }
    // you
    if (m_engine->hasMe()) {
        const QVariantMap sp = screenPos(m_engine->meLon(), m_engine->meLat());
        const QPointF pt(sp.value(u"x"_s).toDouble(), sp.value(u"y"_s).toDouble());
        const QColor you = col("you", accent);
        QColor ringc = you;
        ringc.setAlphaF(0.22);
        p->setPen(Qt::NoPen);
        p->setBrush(ringc);
        p->drawEllipse(pt, 17, 17);
        p->setPen(QPen(halo, 2.5));
        p->setBrush(you);
        p->drawEllipse(pt, 7, 7);
    }
}

} // namespace Taos::Survival
