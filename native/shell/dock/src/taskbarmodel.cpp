// SPDX-License-Identifier: AGPL-3.0-or-later
#include "taskbarmodel.h"

#include <algorithm>

TaskbarModel::TaskbarModel(QObject *parent) : QAbstractListModel(parent) {}

int TaskbarModel::focusedPosition() const {
    for (int i = 0; i < m_buttons.size(); ++i)
        if (m_buttons[i].focused) return i;
    return -1;
}

int TaskbarModel::positionOf(int id) const {
    for (int i = 0; i < m_buttons.size(); ++i)
        if (m_buttons[i].id == id) return i;
    return -1;
}

void TaskbarModel::setWindows(const QVector<WindowInfo> &ws) {
    QHash<int, const WindowInfo *> byId;
    QVector<int> fresh;
    for (const WindowInfo &w : ws) {
        byId.insert(w.id, &w);
        if (!m_order.contains(w.id)) fresh << w.id;
    }
    std::sort(fresh.begin(), fresh.end());      // windows that appear together: lowest id (oldest) first
    QVector<int> order;
    for (int id : std::as_const(m_order)) if (byId.contains(id)) order << id;
    order += fresh;
    QVector<TaskbarButton> next;
    for (int id : std::as_const(order)) {
        const WindowInfo &w = *byId.value(id);
        const DesktopEntry *e = m_idx ? m_idx->matchWindow(w.appId) : nullptr;
        TaskbarButton b;
        b.id = id;
        b.appId = w.appId;
        b.name = e ? e->name : w.appId;
        b.title = w.title.isEmpty() ? b.name : w.title;
        b.icon = e ? e->icon : w.appId;
        b.focused = w.focused && !w.minimised;
        b.minimised = w.minimised;
        next << b;
    }
    m_order = order;
    if (next == m_buttons) return;
    beginResetModel();
    m_buttons = next;
    endResetModel();
    emit changed();
}

QVariant TaskbarModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() >= m_buttons.size()) return {};
    const TaskbarButton &b = m_buttons[i.row()];
    switch (role) {
    case IdRole: return b.id;
    case TitleRole: return b.title;
    case NameRole: return b.name;
    case IconRole: return b.icon;
    case FocusedRole: return b.focused;
    case MinimisedRole: return b.minimised;
    case AccessibleRole:
        return b.minimised ? tr("%1, minimised").arg(b.title) : b.focused ? tr("%1, active").arg(b.title) : b.title;
    case TooltipRole: return b.title == b.name ? b.title : b.name + QStringLiteral(": ") + b.title;
    }
    return {};
}

QHash<int, QByteArray> TaskbarModel::roleNames() const {
    return {{IdRole, "windowId"}, {TitleRole, "title"}, {NameRole, "name"}, {IconRole, "icon"}, {FocusedRole, "focused"},
            {MinimisedRole, "minimised"}, {AccessibleRole, "accessibleText"}, {TooltipRole, "tooltip"}};
}

TaskbarLayout::Result TaskbarLayout::compute(int available, int count, int focused, int minW, int maxW, int gap, int moreW) {
    Result r;
    if (count <= 0 || available <= 0) return r;
    const int fitAll = (available - (count - 1) * gap) / count;
    if (fitAll >= minW) {
        r.buttonWidth = std::min(maxW, fitAll);
        r.visible = count;
        return r;
    }
    r.buttonWidth = minW;
    const int room = available - moreW - gap;
    r.visible = std::max(1, std::min(count, (room + gap) / (minW + gap)));
    r.hidden = count - r.visible;
    if (focused >= r.visible) r.first = focused - r.visible + 1;     // keep the active window in view
    r.first = std::max(0, std::min(r.first, count - r.visible));
    return r;
}
