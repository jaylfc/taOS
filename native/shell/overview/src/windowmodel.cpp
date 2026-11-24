// SPDX-License-Identifier: AGPL-3.0-or-later
#include "windowmodel.h"

QHash<int, QByteArray> WindowModel::roleNames() const {
    return { { ConIdRole, "conId" }, { TitleRole, "title" }, { AppIdRole, "appId" }, { AppNameRole, "appName" },
             { IconRole, "iconSource" }, { IdentifierRole, "identifier" }, { WorkspaceRole, "workspace" },
             { WorkspaceNumRole, "workspaceNum" }, { OutputRole, "output" }, { FocusedRole, "focused" },
             { UrgentRole, "urgent" }, { FloatingRole, "floating" }, { AccessibleRole, "accessibleText" },
             { RectRole, "rect" }, { WsRectRole, "wsRect" }, { MinimisedRole, "minimised" } };
}

QString WindowModel::accessibleText(const WinInfo &w) const {
    const QString app = m_apps ? m_apps->displayName(w.appId) : w.appId;
    QString t = app;
    if (!w.title.isEmpty() && w.title != app) t += QStringLiteral(": ") + w.title;
    t += QStringLiteral(", workspace ") + w.workspace;
    if (w.minimised) t += QStringLiteral(", minimised");
    if (w.urgent) t += QStringLiteral(", needs attention");
    return t;
}

QVariant WindowModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() >= m_rows.size()) return {};
    const WinInfo &w = m_rows[i.row()];
    switch (role) {
    case ConIdRole: return w.conId;
    case TitleRole: return w.title;
    case AppIdRole: return w.appId;
    case AppNameRole: return m_apps ? m_apps->displayName(w.appId) : w.appId;
    case IconRole: return m_icon ? m_icon(w.appId) : QString();
    case IdentifierRole: return w.identifier;
    case WorkspaceRole: return w.workspace;
    case WorkspaceNumRole: return w.workspaceNum;
    case OutputRole: return w.output;
    case FocusedRole: return w.focused;
    case UrgentRole: return w.urgent;
    case FloatingRole: return w.floating;
    case MinimisedRole: return w.minimised;
    case AccessibleRole: return accessibleText(w);
    case RectRole: return QRectF(w.rect);
    case WsRectRole: return QRectF(m_wsRects.value(w.output + QLatin1Char('\n') + w.workspace));
    }
    return {};
}

int WindowModel::rowOf(int conId) const {
    for (int i = 0; i < m_rows.size(); ++i) if (m_rows[i].conId == conId) return i;
    return -1;
}

bool WindowModel::setWindows(const QVector<WinInfo> &ordered, bool keepOrder) {
    if (!keepOrder) {
        bool same = ordered.size() == m_rows.size();
        for (int i = 0; same && i < ordered.size(); ++i) same = ordered[i].conId == m_rows[i].conId;
        beginResetModel();
        m_rows = ordered;
        endResetModel();
        return !same;
    }
    bool changed = false;
    // remove the vanished
    for (int i = m_rows.size() - 1; i >= 0; --i) {
        bool found = false;
        for (const WinInfo &w : ordered) if (w.conId == m_rows[i].conId) { found = true; break; }
        if (!found) {
            beginRemoveRows({}, i, i);
            m_rows.removeAt(i);
            endRemoveRows();
            changed = true;
        }
    }
    for (const WinInfo &w : ordered) {
        const int r = rowOf(w.conId);
        if (r < 0) {
            beginInsertRows({}, int(m_rows.size()), int(m_rows.size()));
            m_rows.push_back(w);
            endInsertRows();
            changed = true;
        } else if (!(m_rows[r] == w)) {
            m_rows[r] = w;
            emit dataChanged(index(r), index(r));
        }
    }
    return changed;
}
