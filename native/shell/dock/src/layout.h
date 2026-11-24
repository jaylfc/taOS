// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QMargins>
#include <QPoint>
#include <QRect>
#include <QSize>
#include <QString>

// Where the dock goes and how much room it keeps clear. All numbers are logical pixels (what the
// layer-shell protocol uses); Layout::physical() gives device pixels for an output scale.
struct LayoutInput {
    QString position = QStringLiteral("bottom");   // bottom | left | right
    QString size = QStringLiteral("medium");       // small | medium | large
    bool attached = false;
    bool autohide = false;
    bool magnify = false;
    int items = 0;                                 // application and minimised slots
    int separators = 0;
    QSize output = QSize(1920, 1080);              // logical size of the output
};

struct Layout {
    bool vertical = false;        // strip runs top to bottom (left and right docks)
    int iconSize = 48;
    int cell = 60;                // one item slot along the strip
    int separatorWidth = 14;
    int padding = 10;             // inside the strip, along it
    int gap = 8;                  // floating: distance between the strip and the screen edge (a layer-shell margin); 0 when attached
    int radius = 18;              // strip corner radius (outer corners only when attached)
    int stripCross = 70;          // strip thickness (across)
    int stripMain = 0;            // strip length (along)
    int band = 0;                 // transparent room beside the strip: only magnification needs any (else the surface IS the strip)
    int headroom = 0;             // growth room for magnified icons (equals `band`)
    int edge = 0;                 // extra room at both ends of the strip for magnified end items
    QSize surface;                // the layer surface: the strip (plus magnification headroom)
    QSize hiddenSurface;          // the surface while auto-hidden: a thin line on the screen edge that catches the pointer
    QRect strip;                  // the strip inside the surface
    int margin = 8;               // layer-shell margin on the anchored edge (= gap)
    bool anchorTop = false, anchorBottom = false, anchorLeft = false, anchorRight = false;
    int exclusiveZone = 0;        // 0 when auto-hiding. The compositor adds the margin: the cleared space is reserved()
    int reserved() const { return exclusiveZone > 0 ? exclusiveZone + margin : 0; }
    // Surface position in the output's logical coordinates.
    QPoint surfacePos(const QSize &output) const;
    QSize physical(qreal scale) const;
    int physicalExclusive(qreal scale) const;
    // Offset that moves the strip fully off the screen along its cross axis (auto-hide slide).
    int slideDistance() const { return stripCross + gap + band + 2; }
};

namespace DockLayout {
int iconFor(const QString &size);   // 36 / 48 / 64, unknown gives medium
Layout compute(const LayoutInput &in);
constexpr int kTriggerPx = 2;
}
