#pragma once
#include <QColor>

namespace Contrast {
double luminance(const QColor &c);
double ratio(const QColor &a, const QColor &b);
// `fg` (with its alpha) composited over opaque `bg`.
QColor over(const QColor &fg, const QColor &bg);
}
