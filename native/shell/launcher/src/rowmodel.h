// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QAbstractListModel>

#include "engine.h"

namespace taos::launcher {

// The list the QML shows: one row per result, with the section label on the first row of a group.
class RowModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Roles { SectionRole = Qt::UserRole + 1, FirstInSectionRole, TitleRole, SubtitleRole, IconRole, HasIconRole, KindRole,
                 HintRole, IdRole, AccessibleRole, TopHitRole };
    using QAbstractListModel::QAbstractListModel;
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : m_rows.size(); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    void setRows(const QList<taos::search::Row> &rows);
    const taos::search::Row *at(int i) const { return i >= 0 && i < m_rows.size() ? &m_rows[i] : nullptr; }
    int indexOfId(const QString &id) const;
    int count() const { return m_rows.size(); }
    static QString kindLabel(const QString &kind);
    // Icon names the platform theme can draw (cached). Empty URL = draw a monogram.
    static QString iconUrl(const QString &icon);
private:
    QList<taos::search::Row> m_rows;
};

}  // namespace taos::launcher
