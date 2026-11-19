#include "glass.h"

#include <cmath>

GlassSpec GlassSpec::forTheme(const Theme &t) {
    GlassSpec g;
    const bool light = t.variant() == QLatin1String("light");
    g.tint = t.ground();
    g.tint.setAlphaF(light ? 0.62 : 0.52);
    g.veil = light ? QColor(255, 255, 255, 0) : QColor(255, 255, 255, 28);  // up to 11% light on dark glass (top edge; fades to 4%)
    g.stroke = t.text();
    g.stroke.setAlphaF(light ? 0.30 : 0.24);
    g.text = t.text();
    g.placeholder = t.textSecondary();
    g.scrim = light ? QColor(255, 255, 255, 140) : QColor(0, 0, 0, 89);  // 55% / 35%
    g.blurRadius = 64;
    return g;
}

namespace Contrast {
static double lin(double c) { return c <= 0.03928 ? c / 12.92 : std::pow((c + 0.055) / 1.055, 2.4); }
double luminance(const QColor &c) { return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF()); }
double ratio(const QColor &a, const QColor &b) {
    const double la = luminance(a), lb = luminance(b);
    return (std::max(la, lb) + 0.05) / (std::min(la, lb) + 0.05);
}
QColor over(const QColor &fg, const QColor &bg) {
    const qreal a = fg.alphaF();
    return QColor::fromRgbF(fg.redF() * a + bg.redF() * (1 - a), fg.greenF() * a + bg.greenF() * (1 - a),
                            fg.blueF() * a + bg.blueF() * (1 - a));
}
}  // namespace Contrast
