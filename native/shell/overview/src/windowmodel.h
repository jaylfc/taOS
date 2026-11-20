#pragma once
#include <QAbstractListModel>
#include <QVector>

#include "appinfo.h"
#include "swaytree.h"

// The windows the overlay shows, in MRU order. Rows are updated in place while the overlay is visible
// (so nothing jumps under the pointer) and re-sorted when it opens.
class WindowModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Roles { ConIdRole = Qt::UserRole + 1, TitleRole, AppIdRole, AppNameRole, IconRole, IdentifierRole,
                 WorkspaceRole, WorkspaceNumRole, OutputRole, FocusedRole, UrgentRole, FloatingRole,
                 AccessibleRole, RectRole, WsRectRole };
    explicit WindowModel(const AppInfo *apps, QObject *parent = nullptr) : QAbstractListModel(parent), m_apps(apps) {}
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : int(m_rows.size()); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    const WinInfo &at(int row) const { return m_rows[row]; }
    int rowOf(int conId) const;
    const QVector<WinInfo> &all() const { return m_rows; }
    // `ordered` is the full set in the wanted order. With keepOrder, existing rows stay where they are,
    // missing ones are removed and new ones appended; otherwise the model is reset to `ordered`.
    // Returns true when the set of windows changed.
    bool setWindows(const QVector<WinInfo> &ordered, bool keepOrder);
    void setIconResolver(std::function<QString(const QString &)> f) { m_icon = std::move(f); }
    QString accessibleText(const WinInfo &w) const;
    void setWorkspaceRects(const QHash<QString, QRect> &r) { m_wsRects = r; }
private:
    const AppInfo *m_apps;
    QVector<WinInfo> m_rows;
    std::function<QString(const QString &)> m_icon;
    QHash<QString, QRect> m_wsRects;
};
