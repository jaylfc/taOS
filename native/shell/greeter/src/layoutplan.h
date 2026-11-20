#pragma once
#include <QObject>
#include <QRectF>
#include <QSizeF>

// Screen layout. Every shipped wallpaper has the "taOS" wordmark rendered into
// the image around the centre, so the clock sits above it and the user
// selector below it; nothing is allowed over it. The boxes below were measured
// from the shipped images (neural, 3840x1200, 3840x2160, 1080x2400) and include
// a safety margin. They are fractions of the screen size.
namespace LayoutMath {
enum class Shape { Ultrawide, Landscape, Portrait };
Shape shapeFor(const QSizeF &screen);
// The box (screen pixels) that the wordmark, with margin, occupies.
QRectF wordmarkRect(const QSizeF &screen);
// UI scale: 1.0 is a 1920x1080 desktop. Portrait screens scale from the width.
qreal uiScale(const QSizeF &screen);

struct Plan {
    QSizeF screen;
    qreal s = 1;           // ui scale
    QRectF wordmark;
    QRectF clockBlock;     // clock, date and weather
    qreal clockPx = 0, datePx = 0, weatherPx = 0;
    QRectF userBlock;      // avatars, names, password field, message
    qreal avatar = 96;     // avatar diameter
    qreal fieldW = 0, fieldH = 0;
    qreal nameH = 0, captionH = 0;  // name line and the caption under the selected name
    qreal tileH = 0;       // avatar + name (+ caption) block height
    qreal fieldTop = 0;    // field offset from userBlock.top
    qreal messageH = 0;
    QRectF bottomBar;      // hostname (left) and power buttons (right)
    qreal margin = 0;
};
Plan plan(const QSizeF &screen);
}  // namespace LayoutMath

// LayoutPlan: the plan for QML. Set `width` and `height`, read the rest.
class LayoutPlan : public QObject {
    Q_OBJECT
    Q_PROPERTY(qreal width READ width WRITE setWidth NOTIFY changed)
    Q_PROPERTY(qreal height READ height WRITE setHeight NOTIFY changed)
    Q_PROPERTY(qreal s READ s NOTIFY changed)
    Q_PROPERTY(QRectF wordmark READ wordmark NOTIFY changed)
    Q_PROPERTY(QRectF clockBlock READ clockBlock NOTIFY changed)
    Q_PROPERTY(QRectF userBlock READ userBlock NOTIFY changed)
    Q_PROPERTY(QRectF bottomBar READ bottomBar NOTIFY changed)
    Q_PROPERTY(qreal clockPx READ clockPx NOTIFY changed)
    Q_PROPERTY(qreal datePx READ datePx NOTIFY changed)
    Q_PROPERTY(qreal weatherPx READ weatherPx NOTIFY changed)
    Q_PROPERTY(qreal avatar READ avatar NOTIFY changed)
    Q_PROPERTY(qreal fieldW READ fieldW NOTIFY changed)
    Q_PROPERTY(qreal fieldH READ fieldH NOTIFY changed)
    Q_PROPERTY(qreal nameH READ nameH NOTIFY changed)
    Q_PROPERTY(qreal captionH READ captionH NOTIFY changed)
    Q_PROPERTY(qreal tileH READ tileH NOTIFY changed)
    Q_PROPERTY(qreal fieldTop READ fieldTop NOTIFY changed)
    Q_PROPERTY(qreal margin READ margin NOTIFY changed)
public:
    explicit LayoutPlan(QObject *parent = nullptr) : QObject(parent) {}
    qreal width() const { return m_p.screen.width(); }
    qreal height() const { return m_p.screen.height(); }
    void setWidth(qreal w) { set(w, height()); }
    void setHeight(qreal h) { set(width(), h); }
    qreal s() const { return m_p.s; }
    QRectF wordmark() const { return m_p.wordmark; }
    QRectF clockBlock() const { return m_p.clockBlock; }
    QRectF userBlock() const { return m_p.userBlock; }
    QRectF bottomBar() const { return m_p.bottomBar; }
    qreal clockPx() const { return m_p.clockPx; }
    qreal datePx() const { return m_p.datePx; }
    qreal weatherPx() const { return m_p.weatherPx; }
    qreal avatar() const { return m_p.avatar; }
    qreal fieldW() const { return m_p.fieldW; }
    qreal fieldH() const { return m_p.fieldH; }
    qreal nameH() const { return m_p.nameH; }
    qreal captionH() const { return m_p.captionH; }
    qreal tileH() const { return m_p.tileH; }
    qreal fieldTop() const { return m_p.fieldTop; }
    qreal margin() const { return m_p.margin; }
signals:
    void changed();

private:
    void set(qreal w, qreal h);
    LayoutMath::Plan m_p;
};
