// SPDX-License-Identifier: AGPL-3.0-or-later
#include "layout.h"

#include <algorithm>
#include <cmath>

namespace DockLayout {

int iconFor(const QString &size) {
    if (size == QLatin1String("small")) return 36;
    if (size == QLatin1String("large")) return 64;
    return 48;
}

Layout compute(const LayoutInput &in) {
    Layout l;
    l.iconSize = iconFor(in.size);
    l.vertical = in.position == QLatin1String("left") || in.position == QLatin1String("right");
    l.cell = l.iconSize + 12;
    l.stripCross = l.iconSize + 22;
    l.gap = in.attached ? 0 : 8;
    l.radius = std::max(12, l.iconSize / 3 + 6);
    l.headroom = in.magnify ? int(std::lround(l.iconSize * 0.55)) : 0;
    l.edge = in.magnify ? l.iconSize / 2 : 0;
    l.band = l.headroom;
    l.margin = l.gap;
    const int mainAvail = (l.vertical ? in.output.height() : in.output.width()) - 16;
    l.stripMain = 2 * l.padding + in.items * l.cell + in.separators * l.separatorWidth;
    l.stripMain = std::min(l.stripMain, std::max(l.cell + 2 * l.padding, mainAvail - 2 * l.edge));
    const int surfMain = l.stripMain + 2 * l.edge;
    const int surfCross = l.band + l.stripCross;
    l.surface = l.vertical ? QSize(surfCross, surfMain) : QSize(surfMain, surfCross);
    l.hiddenSurface = l.vertical ? QSize(kTriggerPx, surfMain) : QSize(surfMain, kTriggerPx);

    const QString pos = in.position;
    l.anchorBottom = pos == QLatin1String("bottom");
    l.anchorLeft = pos == QLatin1String("left");
    l.anchorRight = pos == QLatin1String("right");
    // Anchored to one edge only: the compositor centres the surface along it.
    if (l.anchorBottom) l.strip = QRect(l.edge, l.band, l.stripMain, l.stripCross);
    else if (l.anchorLeft) l.strip = QRect(0, l.edge, l.stripCross, l.stripMain);
    else l.strip = QRect(l.band, l.edge, l.stripCross, l.stripMain);
    // The compositor adds the margin to a positive exclusive zone; magnification headroom is not reserved.
    l.exclusiveZone = in.autohide ? 0 : l.stripCross;
    return l;
}

}  // namespace DockLayout

QPoint Layout::surfacePos(const QSize &o) const {
    if (anchorLeft) return QPoint(margin, (o.height() - surface.height()) / 2);
    if (anchorRight) return QPoint(o.width() - surface.width() - margin, (o.height() - surface.height()) / 2);
    return QPoint((o.width() - surface.width()) / 2, o.height() - surface.height() - margin);
}

QSize Layout::physical(qreal scale) const {
    return QSize(int(std::ceil(surface.width() * scale)), int(std::ceil(surface.height() * scale)));
}

int Layout::physicalExclusive(qreal scale) const { return int(std::ceil(reserved() * scale)); }
