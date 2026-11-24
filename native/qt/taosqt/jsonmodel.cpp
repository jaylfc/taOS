// SPDX-License-Identifier: AGPL-3.0-or-later
#include "taosqt/jsonmodel.h"

#include <QJsonDocument>

using namespace Qt::StringLiterals;

namespace Taos {

static constexpr int kItemRole = Qt::UserRole;

JsonListModel::JsonListModel(const QStringList &roles, const QString &keyField, QObject *parent)
    : QAbstractListModel(parent), m_roles(roles), m_keyField(keyField)
{
}

int JsonListModel::rowCount(const QModelIndex &parent) const
{
    return parent.isValid() ? 0 : int(m_rows.size());
}

QHash<int, QByteArray> JsonListModel::roleNames() const
{
    QHash<int, QByteArray> r;
    r.insert(kItemRole, "item");
    for (int i = 0; i < m_roles.size(); ++i)
        r.insert(kItemRole + 1 + i, m_roles.at(i).toUtf8());
    return r;
}

QVariant JsonListModel::data(const QModelIndex &index, int role) const
{
    if (!index.isValid() || index.row() >= m_rows.size())
        return {};
    const QJsonObject &o = m_rows.at(index.row()).obj;
    if (role == kItemRole)
        return o.toVariantMap();
    const int i = role - kItemRole - 1;
    if (i < 0 || i >= m_roles.size())
        return {};
    return o.value(m_roles.at(i)).toVariant();
}

QVariantMap JsonListModel::get(int row) const
{
    return row >= 0 && row < m_rows.size() ? m_rows.at(row).obj.toVariantMap() : QVariantMap();
}

QString JsonListModel::keyOf(const QJsonObject &o) const
{
    const QJsonValue v = o.value(m_keyField);
    if (v.isUndefined() || v.isNull())
        return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
    if (v.isDouble())
        return QString::number(qint64(v.toDouble()));
    return v.toVariant().toString();
}

void JsonListModel::setRoles(const QStringList &roles)
{
    beginResetModel();
    m_roles = roles;
    endResetModel();
}

void JsonListModel::setRows(const QJsonArray &rows)
{
    QList<Row> next;
    QSet<QString> seen;
    for (const QJsonValue &v : rows) {
        if (!v.isObject())
            continue;
        Row r{keyOf(v.toObject()), v.toObject()};
        if (seen.contains(r.key))
            continue;
        seen.insert(r.key);
        next.append(std::move(r));
    }
    const int before = int(m_rows.size());

    // 1. drop rows that are gone (bottom-up keeps indexes valid)
    for (int i = int(m_rows.size()) - 1; i >= 0; --i) {
        if (!seen.contains(m_rows.at(i).key)) {
            beginRemoveRows({}, i, i);
            m_rows.removeAt(i);
            endRemoveRows();
        }
    }
    // 2. walk the new order: update, move or insert
    for (int i = 0; i < next.size(); ++i) {
        if (i < m_rows.size() && m_rows.at(i).key == next.at(i).key) {
            if (m_rows.at(i).obj != next.at(i).obj) {
                m_rows[i].obj = next.at(i).obj;
                Q_EMIT dataChanged(index(i), index(i));
            }
            continue;
        }
        int j = -1;
        for (int k = i + 1; k < m_rows.size(); ++k) {
            if (m_rows.at(k).key == next.at(i).key) {
                j = k;
                break;
            }
        }
        if (j >= 0) {
            beginMoveRows({}, j, j, {}, i);
            m_rows.move(j, i);
            endMoveRows();
            if (m_rows.at(i).obj != next.at(i).obj) {
                m_rows[i].obj = next.at(i).obj;
                Q_EMIT dataChanged(index(i), index(i));
            }
        } else {
            beginInsertRows({}, i, i);
            m_rows.insert(i, next.at(i));
            endInsertRows();
        }
    }
    if (before != m_rows.size())
        Q_EMIT countChanged();
}

} // namespace Taos
