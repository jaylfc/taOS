#include "layoutplan.h"

#include <algorithm>

namespace LayoutMath {

Shape shapeFor(const QSizeF &sz) {
    const qreal ar = sz.height() > 0 ? sz.width() / sz.height() : 1.78;
    if (ar >= 2.3) return Shape::Ultrawide;
    if (ar >= 1.2) return Shape::Landscape;
    return Shape::Portrait;
}

QRectF wordmarkRect(const QSizeF &sz) {
    const qreal W = sz.width(), H = sz.height();
    switch (shapeFor(sz)) {
    case Shape::Ultrawide:  // 3840x1200: glyphs x .445-.555, y .44-.55
        return QRectF(W * 0.43, H * 0.40, W * 0.14, H * 0.20);
    case Shape::Landscape:  // 3840x2160, 1920x1080: glyphs x .40-.595, y .445-.575
        return QRectF(W * 0.385, H * 0.415, W * 0.23, H * 0.185);
    case Shape::Portrait:   // 1080x2400: glyphs x .27-.73, y .41-.47
        return QRectF(W * 0.24, H * 0.385, W * 0.52, H * 0.11);
    }
    return QRectF();
}

qreal uiScale(const QSizeF &sz) {
    if (shapeFor(sz) == Shape::Portrait) return std::clamp(sz.width() / 720.0, 0.75, 3.0);
    return std::clamp(std::min(sz.width() / 1920.0, sz.height() / 1080.0), 0.75, 3.0);
}

Plan plan(const QSizeF &sz) {
    Plan p;
    p.screen = sz;
    const qreal W = sz.width(), H = sz.height();
    p.s = uiScale(sz);
    const qreal s = p.s;
    p.wordmark = wordmarkRect(sz);
    p.margin = std::max(24.0 * s, W * 0.012);

    // Clock block: from the top margin to just above the wordmark box.
    const qreal top = std::max(H * 0.07, 48.0 * s);
    const qreal gap = std::max(H * 0.035, 20.0 * s);
    const qreal availH = std::max(40.0, p.wordmark.top() - gap - top);
    // Block height = clockPx * kBlock (clock line, date, weather row).
    const qreal kBlock = 1.26;  // one info line, then the time
    qreal clockPx = std::min({availH / kBlock, H * 0.15, W * 0.24});
    clockPx = std::max(clockPx, 56.0);
    p.clockPx = clockPx;
    p.datePx = std::max(16.0, clockPx * 0.15);
    p.weatherPx = std::max(15.0, clockPx * 0.14);
    const qreal blockH = std::min(availH, clockPx * kBlock);
    const qreal blockW = std::min(W * 0.92, std::max(clockPx * 3.4, 420.0 * s));
    const qreal y0 = top + (availH - blockH) * 0.5;  // settle toward the top: air above the wordmark
    p.clockBlock = QRectF((W - blockW) / 2, y0, blockW, blockH);

    // User block: below the wordmark box, above the bottom bar.
    // Proportions of a desktop login: a small avatar, the name close under
    // it, and a compact pill about three avatars wide. Nothing shouts.
    p.avatar = 68.0 * s;
    p.fieldH = 34.0 * s;
    p.fieldW = std::min(W * 0.8, 212.0 * s);
    p.nameH = 22.0 * s;
    p.captionH = 0.0;
    p.messageH = 20.0 * s;
    const qreal bottomH = 44.0 * s;
    p.bottomBar = QRectF(p.margin, H - p.margin - bottomH, W - 2 * p.margin, bottomH);
    const qreal ugap = std::max(H * 0.03, 16.0 * s);
    qreal ytop = p.wordmark.bottom() + ugap;
    const qreal ybottom = p.bottomBar.top() - 8.0 * s;
    auto layoutFor = [&](qreal avatar) {
        p.avatar = avatar;
        p.tileH = avatar + 10.0 * s + p.nameH + p.captionH;
        p.fieldTop = p.tileH + 10.0 * s;
        return p.fieldTop + p.fieldH + 10.0 * s + p.messageH;
    };
    qreal need = layoutFor(p.avatar);
    if (ybottom - ytop < need) {
        // Tight screen: shrink the avatar (never below 64 px), never the field.
        const qreal fixed = need - p.avatar;
        layoutFor(std::clamp(ybottom - ytop - fixed, 48.0 * s, p.avatar));
        need = p.fieldTop + p.fieldH + 10.0 * s + p.messageH;
    } else {
        // Room to spare: the sign-in group belongs to the lower part of the
        // screen, well clear of the wordmark, with the message line's space
        // and a little air left above the bottom bar.
        ytop += (ybottom - ytop - need) * 0.72;
    }
    const qreal ublockW = std::min(W * 0.94, 1100.0 * s);
    p.userBlock = QRectF((W - ublockW) / 2, ytop, ublockW, std::min(need, std::max(0.0, ybottom - ytop)));
    return p;
}

}  // namespace LayoutMath

void LayoutPlan::set(qreal w, qreal h) {
    if (w == m_p.screen.width() && h == m_p.screen.height()) return;
    m_p = LayoutMath::plan(QSizeF(w, h));
    emit changed();
}
