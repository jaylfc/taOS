// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QRectF>
#include <QSizeF>
#include <QVector>

// Mission Control style packing: windows keep their aspect ratio and their rough order on the
// screen (top to bottom, then left to right), are scaled by one shared factor and never overlap.
namespace Packing {
struct Item {
    int id = 0;
    QRectF source;   // where the window is on its workspace (any unit); only size and centre matter
};
struct Options {
    qreal gap = 24;          // minimum space between thumbnails, and between rows
    qreal maxScale = 1.0;    // thumbnails are never larger than the real window times this
    QSizeF maxItem;          // no thumbnail is larger than this (0 = no limit): one window must not fill the whole screen
    qreal minDim = 0;        // smaller windows are enlarged so their shorter side reaches this (before fitting)
};
// Returns one rect per input item (same order), all inside `area`, aspect ratios exact,
// deterministic for equal input. An empty input gives an empty result.
QVector<QRectF> pack(const QVector<Item> &items, const QRectF &area, const Options &opt);
}
