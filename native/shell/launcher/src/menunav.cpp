// SPDX-License-Identifier: AGPL-3.0-or-later
#include "menunav.h"

namespace taos::launcher {

bool MenuNav::select(const QString &zone, int index) {
    if (index < 0 || index >= m_count.value(zone)) return false;
    if (zone == m_zone && index == m_index) return false;
    m_zone = zone;
    m_index = index;
    return true;
}

QString MenuNav::nextZone(const QString &from, int dir) const {
    int at = m_order.indexOf(from);
    if (at < 0 && from == m_linkB) at = m_order.indexOf(m_linkA);   // the second pane stands where its partner stands
    for (int i = at + dir; i >= 0 && i < m_order.size(); i += dir)
        if (m_count.value(m_order[i]) > 0) return m_order[i];
    return {};
}

void MenuNav::clampIfNeeded() {
    if (m_zone.isEmpty()) return;
    const int n = m_count.value(m_zone);
    if (n <= 0) { clear(); return; }
    if (m_index >= n) m_index = n - 1;
}

bool MenuNav::move(int dx, int dy) {
    if (!active()) {   // the first arrow key lands on the first thing there is
        for (const QString &z : m_order) if (m_count.value(z) > 0) { m_zone = z; m_index = 0; return true; }
        return false;
    }
    const int cols = m_cols.value(m_zone, 1);
    const int n = m_count.value(m_zone);
    const int row = m_index / cols, col = m_index % cols;
    if (dy != 0) {
        const int ni = m_index + dy * cols;
        if (ni >= 0 && ni < n) { m_index = ni; return true; }
        // the last row may be short: Down from a column that is missing there goes to the last item once
        if (dy > 0 && row < (n - 1) / cols) { m_index = n - 1; return true; }
        const QString z = nextZone(m_zone, dy);
        if (z.isEmpty()) return false;
        const int zc = m_cols.value(z, 1), zn = m_count.value(z);
        m_zone = z;
        m_index = dy > 0 ? qMin(qMin(col, zc - 1), zn - 1) : qMin(((zn - 1) / zc) * zc + qMin(col, zc - 1), zn - 1);
        return true;
    }
    if (dx != 0) {
        const int ni = m_index + dx;
        const bool sameRow = ni >= 0 && ni < n && ni / cols == row;
        if (sameRow) { m_index = ni; return true; }
        const QString other = m_zone == m_linkA ? m_linkB : m_zone == m_linkB ? m_linkA : QString();
        if (!other.isEmpty() && m_count.value(other) > 0) {
            // only at the edge facing the other pane: A is the left pane, B the right
            const bool toRight = m_zone == m_linkA;
            if ((dx > 0) == toRight) {
                const int oc = m_cols.value(other, 1), on = m_count.value(other);
                const int r = qMin(row, (on - 1) / oc);
                m_zone = other;
                m_index = qMin(r * oc + (dx > 0 ? 0 : oc - 1), on - 1);
                return true;
            }
        }
        return false;   // no wrapping sideways: Left at the start of a row stays put
    }
    return false;
}

}  // namespace taos::launcher
