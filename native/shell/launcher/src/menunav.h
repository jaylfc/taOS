// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QHash>
#include <QString>
#include <QStringList>

namespace taos::launcher {

// The 2D cursor of the app menu. The menu is a few zones (pinned grid, recent, all apps, footer buttons); each has a
// count and a number of columns the face reports. The text field keeps the keyboard focus the whole time, so this is
// plain state: no widget focus. Up and Down walk a zone by rows and then cross to the next zone in `order`; Left and
// Right walk a row and, in a two pane layout (`link`), hop to the other pane at the edge.
class MenuNav {
public:
    void setOrder(const QStringList &zones) { m_order = zones; clampIfNeeded(); }
    void setLink(const QString &a, const QString &b) { m_linkA = a; m_linkB = b; }
    void setZone(const QString &name, int count, int columns) { m_count[name] = count; m_cols[name] = qMax(1, columns); clampIfNeeded(); }
    QString zone() const { return m_zone; }
    int index() const { return m_index; }
    bool active() const { return !m_zone.isEmpty() && m_index >= 0; }
    void clear() { m_zone.clear(); m_index = -1; }
    bool select(const QString &zone, int index);
    // dx, dy are -1, 0 or 1. Returns true when the cursor changed.
    bool move(int dx, int dy);
    int count(const QString &z) const { return m_count.value(z); }
    int columns(const QString &z) const { return m_cols.value(z, 1); }
private:
    QString nextZone(const QString &from, int dir) const;
    void clampIfNeeded();
    QStringList m_order;
    QString m_linkA, m_linkB;
    QHash<QString, int> m_count, m_cols;
    QString m_zone;
    int m_index = -1;
};

}  // namespace taos::launcher
