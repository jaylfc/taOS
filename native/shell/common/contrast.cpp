// SPDX-License-Identifier: AGPL-3.0-or-later
#include "contrast.h"

#include <algorithm>
#include <cmath>

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
