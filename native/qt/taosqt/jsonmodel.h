#pragma once
#include <QAbstractListModel>
#include <QJsonArray>
#include <QJsonObject>

#include "taosqt/global.h"

namespace Taos {

/// A list model over an array of JSON objects, for QML (`ListView`).
///
/// Every key in `roles` is a role of the same name (`model.title`), plus the
/// whole row as `item` (a map). `setRows()` diffs by `keyField` so a refetch
/// updates, inserts, removes and moves rows in place instead of resetting the
/// view (scroll position and selection survive).
class TAOSQT_EXPORT JsonListModel : public QAbstractListModel
{
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)
public:
    explicit JsonListModel(const QStringList &roles, const QString &keyField = QStringLiteral("id"),
                           QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = {}) const override;
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    void setRows(const QJsonArray &rows);
    Q_INVOKABLE QVariantMap get(int row) const;
    QJsonObject row(int row) const { return m_rows.value(row).obj; }

Q_SIGNALS:
    void countChanged();

private:
    struct Row {
        QString key;
        QJsonObject obj;
    };
    QString keyOf(const QJsonObject &o) const;

    QStringList m_roles;
    QString m_keyField;
    QList<Row> m_rows;
};

} // namespace Taos
