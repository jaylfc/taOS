#pragma once
#include <QColor>
#include <QObject>

#include "theme.h"

// The numbers behind the frosted glass and the scrims, in one place so the QML
// draws exactly what the contrast test measured.
struct GlassSpec {
    QColor tint;          // painted over the blurred wallpaper (alpha included)
    QColor veil;          // painted over the tint: a faint light that makes dark glass read as glass
    QColor stroke;        // 1 px hairline
    QColor text;          // primary text on glass
    QColor placeholder;   // secondary text on glass
    QColor scrim;         // darkening (dark) or lightening (light) behind bare text
    qreal blurRadius;     // px, MultiEffect blurMax
    static GlassSpec forTheme(const Theme &t);
};

// QML face of the above, plus the theme colours the greeter paints with.
class Style : public QObject {
    Q_OBJECT
    Q_PROPERTY(QColor ground READ ground CONSTANT)
    Q_PROPERTY(QColor text READ text CONSTANT)
    Q_PROPERTY(QColor textSecondary READ textSecondary CONSTANT)
    Q_PROPERTY(QColor textTertiary READ textTertiary CONSTANT)
    Q_PROPERTY(QColor stroke READ stroke CONSTANT)
    Q_PROPERTY(QColor accent READ accent CONSTANT)
    Q_PROPERTY(QColor danger READ danger CONSTANT)
    Q_PROPERTY(QColor warning READ warning CONSTANT)
    Q_PROPERTY(QColor glassTint READ glassTint CONSTANT)
    Q_PROPERTY(QColor glassVeil READ glassVeil CONSTANT)
    Q_PROPERTY(QColor glassStroke READ glassStroke CONSTANT)
    Q_PROPERTY(QColor scrim READ scrim CONSTANT)
    Q_PROPERTY(qreal blurRadius READ blurRadius CONSTANT)
    Q_PROPERTY(bool dark READ dark CONSTANT)
    Q_PROPERTY(bool reduceMotion READ reduceMotion CONSTANT)
public:
    explicit Style(const Theme *t, QObject *parent = nullptr) : QObject(parent), m_t(t), m_g(GlassSpec::forTheme(*t)) {}
    QColor ground() const { return m_t->ground(); }
    QColor text() const { return m_t->text(); }
    QColor textSecondary() const { return m_t->textSecondary(); }
    QColor textTertiary() const { return m_t->textTertiary(); }
    QColor stroke() const { return m_t->stroke(); }
    QColor accent() const { return m_t->accent(); }
    QColor danger() const { return m_t->danger(); }
    QColor warning() const { return m_t->warning(); }
    QColor glassTint() const { return m_g.tint; }
    QColor glassVeil() const { return m_g.veil; }
    QColor glassStroke() const { return m_g.stroke; }
    QColor scrim() const { return m_g.scrim; }
    qreal blurRadius() const { return m_g.blurRadius; }
    bool dark() const { return m_t->variant() != QLatin1String("light"); }
    bool reduceMotion() const { return m_t->reduceMotion(); }
private:
    const Theme *m_t;
    GlassSpec m_g;
};

namespace Contrast {
double luminance(const QColor &c);
double ratio(const QColor &a, const QColor &b);
// `fg` (with its alpha) composited over opaque `bg`.
QColor over(const QColor &fg, const QColor &bg);
}
