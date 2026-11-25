// SPDX-License-Identifier: AGPL-3.0-or-later
#include "packing.h"

#include <algorithm>
#include <cmath>
#include <numeric>

namespace Packing {

namespace {
struct Row { QVector<int> idx; qreal w = 0; qreal h = 0; };

// Splits `order` into k rows of similar total width.
QVector<Row> split(const QVector<int> &order, const QVector<QSizeF> &sz, int k, qreal gap) {
    qreal total = 0;
    for (int i : order) total += sz[i].width() + gap;
    const qreal target = total / k;
    QVector<Row> rows(1);
    for (int n = 0; n < order.size(); ++n) {
        const int i = order[n];
        Row &r = rows.last();
        const bool lastRow = rows.size() == k;
        const qreal with = r.w + sz[i].width() + gap;
        // start a new row when this one is full enough and items remain for the other rows
        if (!r.idx.isEmpty() && !lastRow && with - target / 2 > target && order.size() - n >= k - rows.size()) {
            rows.push_back(Row());
        }
        Row &c = rows.last();
        c.idx.push_back(i);
        c.w += sz[i].width() + gap;
        c.h = std::max(c.h, sz[i].height());
    }
    for (Row &r : rows) r.w -= gap;  // no trailing gap
    return rows;
}
}

QVector<QRectF> pack(const QVector<Item> &items, const QRectF &area, const Options &opt) {
    const int n = items.size();
    QVector<QRectF> out(n);
    if (n == 0 || area.width() <= 0 || area.height() <= 0) return out;

    // Natural sizes, with small windows lifted towards minDim so they stay readable.
    QVector<QSizeF> sz(n);
    for (int i = 0; i < n; ++i) {
        QSizeF s = items[i].source.size();
        if (s.width() < 1) s.setWidth(1);
        if (s.height() < 1) s.setHeight(1);
        const qreal shortSide = std::min(s.width(), s.height());
        if (opt.minDim > 0 && shortSide < opt.minDim) s *= opt.minDim / shortSide;
        sz[i] = s;
    }
    // Reading order: rows by the centre's y (bucketed so a few pixels do not reorder a row), then x.
    QVector<int> order(n);
    std::iota(order.begin(), order.end(), 0);
    qreal avgH = 0;
    for (int i = 0; i < n; ++i) avgH += items[i].source.height();
    avgH = std::max<qreal>(1, avgH / n);
    std::stable_sort(order.begin(), order.end(), [&](int a, int b) {
        const qreal ca = items[a].source.center().y(), cb = items[b].source.center().y();
        const qreal ba = std::floor(ca / (avgH * 0.5)), bb = std::floor(cb / (avgH * 0.5));
        if (ba != bb) return ba < bb;
        const qreal xa = items[a].source.center().x(), xb = items[b].source.center().x();
        if (xa != xb) return xa < xb;
        return items[a].id < items[b].id;
    });

    qreal bestScale = -1;
    QVector<Row> bestRows;
    for (int k = 1; k <= n; ++k) {
        const QVector<Row> rows = split(order, sz, k, opt.gap);
        qreal maxW = 0, sumH = 0;
        for (const Row &r : rows) { maxW = std::max(maxW, r.w); sumH += r.h; }
        const int nr = rows.size();
        // Gaps do not scale: solve s*maxW + gaps <= W, s*sumH + (nr-1)*gap <= H per row.
        qreal sW = 1e9;
        for (const Row &r : rows) {
            const qreal gaps = (r.idx.size() - 1) * opt.gap;
            sW = std::min(sW, (area.width() - gaps) / (r.w - gaps));
        }
        const qreal sH = (area.height() - (nr - 1) * opt.gap) / sumH;
        qreal s = std::min(sW, sH);
        if (s <= 0) continue;
        if (s > bestScale + 1e-9) { bestScale = s; bestRows = rows; }
    }
    if (bestRows.isEmpty()) {  // area smaller than the gaps: degenerate, shrink with no gap
        bestRows = split(order, sz, 1, 0);
        bestScale = std::max<qreal>(1e-3, area.width() / std::max<qreal>(1, bestRows[0].w));
    }
    // The factor is bounded by maxScale relative to the real window size (taken from the first item's
    // natural/lifted ratio is not needed: sz is already in the source unit).
    qreal s = std::min(bestScale, opt.maxScale);
    if (opt.maxItem.width() > 0 && opt.maxItem.height() > 0)
        for (const QSizeF &z : sz) s = std::min(s, std::min(opt.maxItem.width() / z.width(), opt.maxItem.height() / z.height()));

    qreal blockH = 0;
    for (const Row &r : bestRows) blockH += r.h * s;
    blockH += (bestRows.size() - 1) * opt.gap;
    qreal y = area.top() + std::max<qreal>(0, (area.height() - blockH) / 2);
    for (const Row &r : bestRows) {
        qreal rowW = 0;
        for (int i : r.idx) rowW += sz[i].width() * s;
        rowW += (r.idx.size() - 1) * opt.gap;
        qreal x = area.left() + std::max<qreal>(0, (area.width() - rowW) / 2);
        const qreal rh = r.h * s;
        for (int i : r.idx) {
            const qreal w = sz[i].width() * s, h = sz[i].height() * s;
            out[i] = QRectF(x, y + (rh - h) / 2, w, h);   // centred vertically in its row
            x += w + opt.gap;
        }
        y += rh + opt.gap;
    }
    return out;
}
}
